"""Keep singleton/navigation caches from leaking across isolated QA test cases."""
import unittest
from django.core.cache import cache
from django.test.runner import DiscoverRunner

class TransferQARunner(DiscoverRunner):
    def get_resultclass(self):
        base = super().get_resultclass() or unittest.TextTestResult
        class IsolatedCacheResult(base):
            def startTest(self, test):
                cache.clear()
                super().startTest(test)
        return IsolatedCacheResult
