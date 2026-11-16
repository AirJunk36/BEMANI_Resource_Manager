"""Non-parameter Cubism curves, mirrored into the independent render model."""
from bisect import bisect_right
from dataclasses import dataclass

@dataclass
class Curve:
    target:str
    name:str
    initial:tuple
    segments:list
    ends:list

    @classmethod
    def read(cls,row):
        raw=row['Segments'];start=tuple(raw[:2]);initial=start;segments=[];ends=[];i=2
        while i<len(raw):
            tag=int(raw[i]);n=7 if tag==1 else 3
            end=tuple(raw[i+n-2:i+n]);controls=tuple(raw[i+1:i+n-2])
            segments.append((tag,start,controls,end));ends.append(end[0]);start=end;i+=n
        return cls(row['Target'],row['Id'],initial,segments,ends)

    def at(self,time):
        if time<=self.initial[0] or not self.segments:return self.initial[1]
        index=bisect_right(self.ends,time)
        if index>=len(self.segments):return self.segments[-1][3][1]
        tag,start,controls,end=self.segments[index]
        if tag==2:return start[1]
        if tag==3:return end[1]
        u=max(0,min(1,(time-start[0])/max(1e-10,end[0]-start[0])))
        if tag==0:return start[1]+(end[1]-start[1])*u
        def cubic(a,b,c,d,t):return (1-t)**3*a+3*(1-t)**2*t*b+3*(1-t)*t*t*c+t**3*d
        lo,hi=0.,1.
        for _ in range(22):
            u=(lo+hi)/2
            if cubic(start[0],controls[0],controls[2],end[0],u)<time:lo=u
            else:hi=u
        return cubic(start[1],controls[1],controls[3],end[1],u)
