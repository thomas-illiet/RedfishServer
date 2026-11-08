"""Run the server with python -m redfish_mock."""

from .cli import main
from .config import credentials, parser

__all__ = ["credentials", "main", "parser"]

if __name__ == "__main__":
    main()
