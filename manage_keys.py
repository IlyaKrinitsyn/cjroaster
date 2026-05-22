import sys
from database import init_db, add_api_key, list_api_keys, revoke_api_key

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python manage_keys.py add <owner_name>    – создать новый ключ")
        print("  python manage_keys.py list                – показать все ключи")
        print("  python manage_keys.py revoke <key>        – отозвать ключ")
        sys.exit(1)

    init_db()

    cmd = sys.argv[1]
    if cmd == "add":
        if len(sys.argv) < 3:
            print("Укажите имя владельца: python manage_keys.py add 'Алексей'")
            sys.exit(1)
        owner = sys.argv[2]
        new_key = secrets.token_hex(16)  # 32-символьный ключ
        add_api_key(new_key, owner)
        print(f"✅ Ключ создан для {owner}: {new_key}")
    elif cmd == "list":
        keys = list_api_keys()
        if not keys:
            print("Нет ключей.")
        else:
            for k in keys:
                status = "активен" if k["active"] else "отозван"
                print(f"[{k['id']}] {k['key']} – {k['owner_name']} ({status})")
    elif cmd == "revoke":
        if len(sys.argv) < 3:
            print("Укажите ключ для отзыва: python manage_keys.py revoke <key>")
            sys.exit(1)
        key = sys.argv[2]
        revoke_api_key(key)
        print(f"✅ Ключ {key} отозван.")
    else:
        print(f"Неизвестная команда: {cmd}")

if __name__ == "__main__":
    main()