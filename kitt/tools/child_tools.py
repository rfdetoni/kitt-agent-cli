class ChildTools:
    def __init__(self,manager):self.manager=manager
    def spawn(self,**kwargs):return self.manager.spawn(**kwargs)
