from app.tools.filesystem import ListDirectoryTool


def main():

    tool = ListDirectoryTool()

    result = tool.execute(
        r"C:\Users\ASUS\Downloads"
    )

    print(result)


if __name__ == "__main__":
    main()