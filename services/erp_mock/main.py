import uvicorn

from services.erp_mock.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="0.0.0.0", port=8001)


if __name__ == "__main__":
    main()
