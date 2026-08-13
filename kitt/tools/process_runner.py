import subprocess,tempfile,time
from dataclasses import dataclass
from pathlib import Path
from typing import List,Optional
from kitt.core.cancellation import CancellationToken
@dataclass(frozen=True)
class ProcessResult:
    argv:List[str]; returncode:int; stdout:str; stderr:str
    duration_ms:float; timed_out:bool=False; cancelled:bool=False; truncated:bool=False
class ProcessRunner:
    def __init__(self,root_dir:str,max_output_bytes:int=262144):
        self.root=Path(root_dir).resolve(); self.max_output_bytes=max_output_bytes
    def run(self,argv:List[str],timeout_seconds:int=120,cancellation:Optional[CancellationToken]=None)->ProcessResult:
        if not argv or not all(isinstance(x,str) and x for x in argv): raise ValueError("argv must be a non-empty string list")
        started=time.monotonic()
        with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
            proc=subprocess.Popen(argv,cwd=self.root,stdin=subprocess.DEVNULL,
                stdout=stdout_file,stderr=stderr_file,text=False,shell=False)
            timed_out=cancelled=False
            while proc.poll() is None:
                if cancellation and cancellation.cancelled: cancelled=True; proc.terminate(); break
                if time.monotonic()-started>timeout_seconds: timed_out=True; proc.terminate(); break
                time.sleep(.02)
            if timed_out or cancelled:
                try: proc.wait(timeout=1)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait()
            stdout_file.seek(0); stderr_file.seek(0)
            out=stdout_file.read(self.max_output_bytes+1)
            err=stderr_file.read(self.max_output_bytes+1)
        truncated=len(out)+len(err)>self.max_output_bytes
        remaining=self.max_output_bytes
        out=out[:remaining]; remaining-=len(out); err=err[:max(0,remaining)]
        return ProcessResult(list(argv),proc.returncode if proc.returncode is not None else -1,
            out.decode("utf-8","replace"),err.decode("utf-8","replace"),
            (time.monotonic()-started)*1000,timed_out,cancelled,truncated)
