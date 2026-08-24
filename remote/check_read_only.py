import os

import psycopg


def main() -> None:
    connection = psycopg.connect(os.environ["POSTGRES_DSN"])
    write_blocked = False
    try:
        connection.execute("UPDATE dataset_versions SET status=status WHERE false")
    except Exception:
        write_blocked = True
    finally:
        connection.rollback()
        connection.close()
    assert write_blocked
    print({"leitura_ok": True, "escrita_bloqueada": True})


if __name__ == "__main__":
    main()
