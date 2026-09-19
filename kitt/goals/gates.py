from kitt.tools.process_runner import ProcessRunner
class QualityGateRunner:
    def __init__(self, runner: ProcessRunner): self.runner=runner
    def run(self, argv, timeout_seconds=120):
        return self.runner.run(argv,timeout_seconds=timeout_seconds)
