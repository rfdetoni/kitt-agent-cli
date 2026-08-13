class ContinuationPolicy:
    def should_continue(self,goal,gate_results=()):
        if not goal or goal.state!="ACTIVE": return False
        return all(getattr(r,"returncode",1)==0 for r in gate_results)
