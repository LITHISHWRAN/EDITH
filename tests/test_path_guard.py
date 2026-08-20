from app.security.path_guard import PathGuard


def main():

    guard = PathGuard()

    tests = [
        r"C:\Users\ASUS\Downloads",
        r"C:\Users\ASUS\Documents",
        r"C:\Windows",
        r"C:\Windows\System32",
        r"C:\Program Files",
    ]

    for path in tests:

        result = guard.validate(path)

        print()
        print("PATH:", path)
        print("RESULT:", result)


if __name__ == "__main__":
    main()