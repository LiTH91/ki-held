import asyncio
import unittest
from unittest.mock import patch
from backend.utils.human_mouse import move_mouse_to, click_mouse, scroll_mouse

class TestHumanMouse(unittest.IsolatedAsyncioTestCase):
    @patch('backend.utils.human_mouse.log')
    async def test_move_mouse_to(self, mock_log):
        await move_mouse_to(100, 100)
        # Check if multiple intermediate points were logged
        self.assertTrue(mock_log.called)
        self.assertGreaterEqual(mock_log.call_count, 1)

    @patch('backend.utils.human_mouse.log')
    async def test_click_mouse(self, mock_log):
        await click_mouse()
        # Check if click actions were logged
        self.assertTrue(mock_log.called)
        self.assertGreaterEqual(mock_log.call_count, 2)

    @patch('backend.utils.human_mouse.log')
    async def test_scroll_mouse(self, mock_log):
        await scroll_mouse(500)
        # Check if scroll actions were logged
        self.assertTrue(mock_log.called)
        self.assertGreaterEqual(mock_log.call_count, 1)

if __name__ == '__main__':
    unittest.main()
