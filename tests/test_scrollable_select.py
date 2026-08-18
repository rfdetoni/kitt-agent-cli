"""Unit tests for generic ScrollableSelect component with keyboard and mouse support."""
import unittest

from kitt.ui.components.scrollable_select import ScrollableSelect, SelectOption


class TestScrollableSelect(unittest.TestCase):

    def setUp(self):
        self.options = [
            SelectOption(title="GPT-4o", value="openai/gpt-4o", category="OpenAI", badge="128k ctx │ 🛠 tools"),
            SelectOption(title="Claude 3.7 Sonnet", value="anthropic/claude-3-7-sonnet", category="Anthropic", badge="200k ctx │ 🛠 tools │ 🧠 think"),
            SelectOption(title="DeepSeek V3", value="deepseek/deepseek-v3", category="DeepSeek", badge="64k ctx │ 🛠 tools"),
            SelectOption(title="Qwen 2.5 Coder", value="ollama/qwen2.5-coder", category="Ollama", badge="32k ctx │ 🛠 tools"),
            SelectOption(title="Gemini 2.5 Flash", value="gemini/gemini-2.5-flash", category="Google", badge="1M ctx │ 🛠 tools"),
        ]
        self.select = ScrollableSelect(options=self.options, viewport_size=3)

    def test_fuzzy_filtering_and_clamping(self):
        self.select.set_search_query("sonnet")
        filtered = self.select.get_filtered_options()
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0].title, "Claude 3.7 Sonnet")
        self.assertEqual(self.select.get_selected().title, "Claude 3.7 Sonnet")

    def test_keyboard_navigation(self):
        self.select.set_search_query("")
        self.assertEqual(self.select.selected_index, 0)
        self.select.move(1)
        self.assertEqual(self.select.selected_index, 1)
        self.assertEqual(self.select.input_mode, "keyboard")
        self.select.end()
        self.assertEqual(self.select.selected_index, 4)
        self.select.home()
        self.assertEqual(self.select.selected_index, 0)

    def test_mouse_hover_and_click(self):
        selected_called = []
        self.select.on_select = lambda opt: selected_called.append(opt.value)
        
        # Viewport bounds for index 0 with viewport_size 3 is [0, 3]
        self.select.on_mouse_move(visual_row_offset=1)
        self.assertEqual(self.select.input_mode, "mouse")
        self.assertEqual(self.select.selected_index, 1)

        # Mouse click
        clicked = self.select.on_mouse_click(visual_row_offset=2)
        self.assertEqual(clicked.title, "DeepSeek V3")
        self.assertEqual(selected_called, ["deepseek/deepseek-v3"])

    def test_mouse_wheel_scrolling(self):
        self.select.on_mouse_wheel(1)
        self.assertEqual(self.select.selected_index, 1)
        self.select.on_mouse_wheel(-1)
        self.assertEqual(self.select.selected_index, 0)


if __name__ == "__main__":
    unittest.main()
