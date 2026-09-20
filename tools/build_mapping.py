import argparse

from parity.mapping.builder import build_mapping

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build Parity import mapping")
    parser.add_argument("--online", action="store_true", help="Fetch wheels from PyPI (very slow)")
    args = parser.parse_args()
    
    build_mapping(online=args.online)
