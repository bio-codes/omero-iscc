# -*- coding: utf-8 -*-
"""Blitz/OMERO implementation of IMAGEWALK plane traversal.

This module provides deterministic plane traversal for multi-dimensional bioimage data
stored in OMERO servers, conforming to the IMAGEWALK specification.
"""

from dataclasses import dataclass
from typing import Generator
import logging
import numpy as np


logger = logging.getLogger(__name__)


@dataclass
class Plane:
    """Represents a 2D pixel plane with its position metadata.

    Attributes:
        xy_array: 2D NumPy array of pixel data (Y, X dimensions)
        scene_idx: Scene/series index
        z_depth: Z-stack position (0-based)
        c_channel: Channel index (0-based)
        t_time: Time point index (0-based)
    """

    xy_array: np.ndarray
    scene_idx: int
    z_depth: int
    c_channel: int
    t_time: int


def iter_planes_blitz_fileset(conn, fileset):
    # type: (object, object) -> Generator[Plane, None, None]
    """Iterate over 2D planes in all images of an OMERO fileset following IMAGEWALK traversal order.

    Processes all images in a fileset (corresponding to a bioimage file) and yields planes
    in deterministic order. Each image in the fileset becomes a separate scene.

    Conforms to IMAGEWALK specification for deterministic bioimage traversal.

    :param conn: BlitzGateway connection to OMERO server
    :param fileset: OMERO Fileset object to process
    :return: Generator yielding Plane objects in Z→C→T order for all images
    """
    # Get all images in the fileset
    images = list(fileset.copyImages())

    fileset_id = fileset.getId()
    logger.debug(f"OMERO Fileset {fileset_id} - processing {len(images)} image(s)")

    # Process each image as a scene
    for scene_idx, image in enumerate(images):
        # Yield all planes from this image/scene
        yield from iter_planes_blitz_image(conn, image, scene_idx)


def iter_planes_blitz_image(conn, image, scene_idx=0):
    # type: (object, object, int) -> Generator[Plane, None, None]
    """Iterate over 2D planes in a single OMERO image following IMAGEWALK Z→C→T traversal order.

    Processes a single OMERO image (corresponding to a scene) and yields planes in
    deterministic order:
    - Outermost loop: Z dimension (depth/focal plane)
    - Middle loop: C dimension (channel)
    - Innermost loop: T dimension (time)

    Conforms to IMAGEWALK specification for deterministic bioimage traversal.

    :param conn: BlitzGateway connection to OMERO server
    :param image: OMERO Image object to process
    :param scene_idx: Scene index for the Plane objects (default: 0)
    :return: Generator yielding Plane objects in Z→C→T order
    """
    logger.debug(
        f"OMERO - processing scene {scene_idx}: {image.getName()} (ID: {image.getId()})"
    )

    # Get pixels object and ID
    pixels = image.getPrimaryPixels()
    pixels_id = pixels.getId()

    # Get dimensions
    size_t = image.getSizeT()
    size_c = image.getSizeC()
    size_z = image.getSizeZ()
    size_y = image.getSizeY()
    size_x = image.getSizeX()

    logger.debug(
        f"OMERO - scene {scene_idx}: T={size_t}, C={size_c}, Z={size_z}, Y={size_y}, X={size_x}"
    )

    # Get pixel data type mapping
    dtype_str = str(pixels.getPixelsType().getValue())
    dtype_map = {
        "uint8": np.uint8,
        "uint16": np.uint16,
        "uint32": np.uint32,
        "int8": np.int8,
        "int16": np.int16,
        "int32": np.int32,
        "float": np.float32,
        "double": np.float64,
    }
    np_dtype = dtype_map.get(dtype_str, np.uint8)

    # Create RawPixelsStore service for proper plane access
    rps = conn.c.sf.createRawPixelsStore()

    try:
        # Set the pixels ID to access the pixel data
        rps.setPixelsId(pixels_id, True)  # True = bypass cache

        # Traverse planes in Z→C→T order (IMAGEWALK specification)
        for z in range(size_z):
            for c in range(size_c):
                for t in range(size_t):
                    # Get 2D plane using RawPixelsStore
                    plane_bytes = rps.getPlane(z, c, t)

                    # Convert to numpy array
                    plane_array = np.frombuffer(plane_bytes, dtype=np_dtype)
                    xy_array = plane_array.reshape(size_y, size_x)

                    # Yield Plane object
                    yield Plane(
                        xy_array=xy_array,
                        scene_idx=scene_idx,
                        z_depth=z,
                        c_channel=c,
                        t_time=t,
                    )
    finally:
        # Always close the RawPixelsStore
        rps.close()


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        description="Iterate over planes in OMERO images/filesets following IMAGEWALK traversal order"
    )
    parser.add_argument("server_url", help="OMERO server URL")
    parser.add_argument("--user", default="root", help="OMERO username (default: root)")
    parser.add_argument(
        "--pwd", default="omero", help="OMERO password (default: omero)"
    )
    parser.add_argument("--iid", type=int, help="OMERO Image ID to process")
    parser.add_argument("--fid", type=int, help="OMERO Fileset ID to process")

    args = parser.parse_args()

    # Validate that either --iid or --fid is provided
    if not args.iid and not args.fid:
        parser.error("Either --iid (Image ID) or --fid (Fileset ID) must be provided")
    if args.iid and args.fid:
        parser.error("Cannot specify both --iid and --fid, choose one")

    from omero.gateway import BlitzGateway

    # Configure logger to show debug messages
    logger.remove()
    logger.add(sys.stderr, level="DEBUG")

    # Connect to OMERO
    logger.info(f"Connecting to OMERO server: {args.server_url}")
    conn = BlitzGateway(args.user, args.pwd, host=args.server_url, port=4064)

    if not conn.connect():
        logger.error(f"Failed to connect to OMERO server: {args.server_url}")
        sys.exit(1)

    try:
        plane_count = 0

        if args.fid:
            # Process entire fileset
            logger.info(f"Processing OMERO fileset: {args.fid}")
            fileset = conn.getObject("Fileset", args.fid)
            if not fileset:
                logger.error(f"Fileset {args.fid} not found")
                sys.exit(1)

            for plane in iter_planes_blitz_fileset(conn, fileset):
                plane_count += 1
                logger.info(
                    f"Plane {plane_count}: scene={plane.scene_idx}, z={plane.z_depth}, "
                    f"c={plane.c_channel}, t={plane.t_time}, shape={plane.xy_array.shape}, "
                    f"dtype={plane.xy_array.dtype}"
                )
        else:
            # Process single image
            logger.info(f"Processing OMERO image: {args.iid}")
            image = conn.getObject("Image", args.iid)
            if not image:
                logger.error(f"Image {args.iid} not found")
                sys.exit(1)

            for plane in iter_planes_blitz_image(conn, image):
                plane_count += 1
                logger.info(
                    f"Plane {plane_count}: scene={plane.scene_idx}, z={plane.z_depth}, "
                    f"c={plane.c_channel}, t={plane.t_time}, shape={plane.xy_array.shape}, "
                    f"dtype={plane.xy_array.dtype}"
                )

        logger.info(f"Total planes processed: {plane_count}")

    finally:
        conn.close()
