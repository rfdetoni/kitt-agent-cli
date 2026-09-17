import unittest

from kitt.llm.domain import ProviderConnectionError
from kitt.llm.retry import RetryConfig, RetryPolicy


class AgentContractRetryTerminalTests(unittest.TestCase):
    def test_agent_contract_invalid_502_is_not_retryable(self):
        policy = RetryPolicy(RetryConfig(max_retries=2))
        error = ProviderConnectionError(
            'HTTP 502: Bad Gateway - {"error":{"code":"agent_contract_invalid","message":"violou o contrato de saída"}}'
        )
        self.assertFalse(policy.is_retryable(error))

    def test_generic_503_remains_retryable(self):
        policy = RetryPolicy(RetryConfig(max_retries=2))
        self.assertTrue(policy.is_retryable(ProviderConnectionError("HTTP 503 temporarily unavailable")))


if __name__ == "__main__":
    unittest.main()
