import unittest
from unittest.mock import Mock, patch

import function_app


class SummarizationBudgetTests(unittest.TestCase):
    def _response(self, finish_reason: str, content: str = "Complete summary.") -> Mock:
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [
                {
                    "finish_reason": finish_reason,
                    "message": {"content": content},
                }
            ],
            "usage": {
                "completion_tokens": 1200,
                "completion_tokens_details": {"reasoning_tokens": 300},
            },
        }
        return response

    def test_model_budgets_leave_documented_reasoning_headroom(self):
        self.assertLessEqual(function_app.SUMMARY_CHUNK_TOKENS, function_app.MODEL_MAX_INPUT_TOKENS)
        self.assertLessEqual(function_app.SUMMARY_COMPLETION_BUDGETS[-1], function_app.MODEL_MAX_OUTPUT_TOKENS)
        self.assertGreaterEqual(function_app.SUMMARY_COMPLETION_BUDGETS[0], 25_000)
        self.assertLessEqual(
            function_app.SUMMARY_CHUNK_TOKENS + function_app.SUMMARY_COMPLETION_BUDGETS[-1],
            function_app.MODEL_CONTEXT_TOKENS,
        )

    def test_default_chunks_stay_within_estimated_token_target(self):
        text = " ".join(f"word-{index}" for index in range(20_000))

        chunks = function_app._chunk_text(text)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk.split()) * 1.3 <= function_app.SUMMARY_CHUNK_TOKENS for chunk in chunks))

    @patch("function_app.httpx.post")
    def test_complete_summary_uses_initial_budget_and_minimal_reasoning(self, post: Mock):
        post.return_value = self._response("stop")

        result = function_app._llm_summarize("source", "document", 1, 1, lambda: "token")

        self.assertEqual(result, "Complete summary.")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["max_completion_tokens"], 32_768)
        self.assertEqual(payload["reasoning_effort"], "minimal")

    @patch("function_app.httpx.post")
    def test_length_stop_retries_with_larger_budget(self, post: Mock):
        post.side_effect = [
            self._response("length", "Incomplete"),
            self._response("stop", "Complete after retry."),
        ]

        result = function_app._llm_summarize("source", "document", 1, 1, lambda: "token")

        self.assertEqual(result, "Complete after retry.")
        budgets = [call.kwargs["json"]["max_completion_tokens"] for call in post.call_args_list]
        self.assertEqual(budgets, [32_768, 65_536])

    @patch("function_app.httpx.post")
    def test_repeated_length_stop_is_rejected(self, post: Mock):
        post.side_effect = [self._response("length"), self._response("length")]

        with self.assertRaisesRegex(RuntimeError, "remained incomplete"):
            function_app._llm_summarize("source", "document", 1, 1, lambda: "token")


if __name__ == "__main__":
    unittest.main()
