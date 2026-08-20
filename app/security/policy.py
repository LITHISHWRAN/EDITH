class SecurityPolicy:
    """
    Central authorization layer for EDITH.

    Tools perform the actual operation.
    SecurityPolicy decides whether the operation is allowed.
    """

    def can_execute_application(self, application: str) -> bool:
        """
        Check whether EDITH is allowed to launch an application.

        Phase 2:
        - Reject empty/invalid application names.
        - Do not maintain a hardcoded application whitelist.
        - Actual executable resolution is handled by Windows/PATH.
        """

        if not isinstance(application, str):
            return False

        application = application.strip()

        if not application:
            return False

        return True