import json,socket,threading,tempfile,unittest
from pathlib import Path
from kitt.memory.shared_client import SharedMemoryClient

class SharedClientTest(unittest.TestCase):
 def test_recall(self):
  server=socket.socket();server.bind(("127.0.0.1",0));server.listen(1);host,port=server.getsockname();tmp=tempfile.TemporaryDirectory();token=Path(tmp.name)/"token";token.write_text("abc")
  def serve():
   conn,_=server.accept();data=b""
   while not data.endswith(b"\n"):data+=conn.recv(4096)
   req=json.loads(data);self.assertEqual("memory_recall",req["command"]);conn.sendall(b'{"ok":true,"result":[{"content":"rule","pinned":true}]}\n');conn.close();server.close()
  threading.Thread(target=serve,daemon=True).start();client=SharedMemoryClient(f"{host}:{port}",token,1.0);self.assertEqual("rule",client.recall("ws","rule")[0]["content"]);tmp.cleanup()
if __name__=="__main__":unittest.main()
