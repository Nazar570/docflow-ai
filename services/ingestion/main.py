import uvicorn

from services.ingestion.app import get_app


def main() -> None:
    uvicorn.run(get_app(), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
