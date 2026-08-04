import unittest

from app import app


class AppSmokeTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_login_page_is_available(self):
        response = self.client.get("/login")
        self.assertEqual(response.status_code, 200)

    def test_ticket_endpoints_are_registered(self):
        self.assertIn("new_ticket", app.view_functions)
        self.assertIn("edit_ticket", app.view_functions)
        self.assertIn("view_ticket", app.view_functions)
        self.assertIn("delete_ticket", app.view_functions)


if __name__ == "__main__":
    unittest.main()
