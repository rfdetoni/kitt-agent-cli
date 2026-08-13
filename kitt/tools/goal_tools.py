class GoalTools:
    def __init__(self,service):self.service=service
    def create(self,*args,**kwargs):return self.service.create(*args,**kwargs)
    def finish(self,*args,**kwargs):return self.service.finish(*args,**kwargs)
