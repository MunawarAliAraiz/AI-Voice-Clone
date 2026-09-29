"""PyInstaller entry point; keeps package-relative imports in app.desktop intact."""

from app.desktop import main


if __name__ == "__main__":
    main()
