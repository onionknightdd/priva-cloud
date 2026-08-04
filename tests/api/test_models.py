import unittest

from pydantic import ValidationError

from priva_common.models.agent import AgentRunRequest, AgentRunResponse
from priva_common.models.auth import RegisterRequest


class AgentModelTests(unittest.TestCase):
    def test_request_requires_non_empty_message(self) -> None:
        with self.assertRaises(ValidationError):
            AgentRunRequest(message="")

    def test_response_uses_independent_message_lists(self) -> None:
        first = AgentRunResponse()
        second = AgentRunResponse()

        first.messages.append(
            {
                "type": "assistant",
                "content": [{"type": "text", "text": "hello"}],
            }
        )

        self.assertEqual(len(second.messages), 0)

    def test_registration_resource_request_limits(self) -> None:
        request = RegisterRequest(
            username="resource-limit-test",
            password="long-enough-password",
            cpu_cores=4,
            memory_mb=4096,
        )
        self.assertEqual(request.cpu_cores, 4)
        self.assertEqual(request.memory_mb, 4096)

        minimum_request = RegisterRequest(
            username="resource-minimum-test",
            password="long-enough-password",
            cpu_cores=0.512,
            memory_mb=1024,
        )
        self.assertEqual(minimum_request.cpu_cores, 0.512)
        self.assertEqual(minimum_request.memory_mb, 1024)

        with self.assertRaises(ValidationError):
            RegisterRequest(
                username="cpu-under-limit",
                password="long-enough-password",
                cpu_cores=0.511,
            )

        with self.assertRaises(ValidationError):
            RegisterRequest(
                username="memory-under-limit",
                password="long-enough-password",
                memory_mb=1023,
            )

        with self.assertRaises(ValidationError):
            RegisterRequest(
                username="cpu-over-limit",
                password="long-enough-password",
                cpu_cores=4.1,
            )

        with self.assertRaises(ValidationError):
            RegisterRequest(
                username="memory-over-limit",
                password="long-enough-password",
                memory_mb=4097,
            )


if __name__ == "__main__":
    unittest.main()
