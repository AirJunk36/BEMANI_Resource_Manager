"""Bounded AP2 bytecode interpreter. Scripts can access only movie objects.

The byte layout and built-in names were checked against local AFP libraries
and the public-domain bemaniutils format reader. No Python eval/exec is used.
"""
from dataclasses import dataclass
import json
import math
import operator
from pathlib import Path
import struct

CONSTANTS = {int(k):v for k,v in json.loads(Path(__file__).with_name("action_constants.json").read_text()).items()}
UNDEFINED = object()


@dataclass(frozen=True)
class Ref:
    kind: str
    value: object = None


@dataclass
class Function:
    name: str | None
    flags: int
    parameters: list
    program: object
    owner: object = None


class Program:
    def __init__(self, data, string, inherited=None):
        if len(data)<2 or data[0]!=255:
            raise ValueError("不支持的 AFP 脚本头")
        self.actions = {}
        self.string = string
        self.data = data
        p=2
        self.pool = inherited or []
        if data[1]&1:
            count=struct.unpack_from('<H',data,p)[0]; p+=2
            if count>32768 or p+2*count>len(data): raise ValueError("脚本字符串表无效")
            own=list(struct.unpack_from('<'+str(count)+'H',data,p));p+=2*count
            self.pool=inherited if inherited is not None else own
        self.start=p

        def take(fmt):
            nonlocal p
            count=struct.calcsize('>'+fmt)
            if p+count>len(data): raise ValueError("脚本参数被截断")
            result=struct.unpack_from('>'+fmt,data,p);p+=count
            return result[0] if len(result)==1 else result

        def literal():
            kind=take('B')
            if kind==0: return 0
            if kind==1: return take('f')
            if kind==2: return None
            if kind==3: return UNDEFINED
            if kind==4: return Ref('register',take('B'))
            if kind in {5,6}: return kind==5
            if kind==7: return take('i')
            if kind in {8,9}:
                index=take('B' if kind==8 else 'H')
                if index>=len(self.pool): raise ValueError("脚本字符串索引越界")
                return string(self.pool[index])
            if kind==10: return float('nan')
            if kind in {11,35}: return float('inf') if kind==11 else -float('inf')
            if kind in {12,13,14,15,34}:
                return Ref({12:'this',13:'root',14:'parent',15:'this',34:'global'}[kind])
            if kind==51: return take('q')
            if kind==55: return take('b')
            for first,base in [(16,0x100),(19,0x300),(22,0x400),(25,0x200),(28,0x500),(31,0x600),
                               (36,0x700),(39,0x800),(42,0x900),(45,0xa00),(48,0xb00),(52,0xc00),(56,0xd00)]:
                if first<=kind<=first+2:
                    number=base+take('B'); alias=None
                    if kind!=first:
                        index=take('B' if kind==first+1 else 'H')
                        if index>=len(self.pool): raise ValueError("脚本别名索引越界")
                        alias=string(self.pool[index])
                    return CONSTANTS.get(number,alias or f"AFP常量0x{number:x}")
            raise ValueError(f"未实现的脚本值类型：0x{kind:02x}")

        while p<len(data):
            start=p; code=take('B'); args=None
            if code==0:
                self.actions[start]=(code,args,p);break
            if code==67: args=[literal() for _ in range(take('B'))]
            elif code==64:
                flags,name,count,registers,length=take('HHHBH')
                parameters=[take('BH') for _ in range(count)]
                body=data[p:p+length]
                if len(body)!=length: raise ValueError("脚本函数被截断")
                args=Function(string(name) if name else None,flags,parameters,Program(body,string,self.pool));p+=length
            elif code in {63,75}: args=[take('B') for _ in range(take('B'))]
            elif code in {74,69}: args=take('B')
            elif code in {21,77}: args=take('b')
            elif code==76: args=take('Bb')
            elif code in {68,70}:
                offset=take('h');args=p+offset
            elif code==73:
                kind,offset=take('Bh');args=(kind,p+offset)
            elif code==71:
                flags=take('B');args=(bool(flags&1),take('H') if flags&2 else 0)
            elif code==66:
                length=take('H');args=data[p:p+length];p+=length
            elif code not in {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,22,23,24,25,26,
                              27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,
                              50,51,52,53,54,55,56,57,58,59,60,61,62,72}:
                raise ValueError(f"未实现的脚本指令：0x{code:02x}")
            if p>len(data): raise ValueError("脚本范围无效")
            self.actions[start]=(code,args,p)
            if len(self.actions)>100000: raise ValueError("脚本指令数量超限")
        for code,args,_ in self.actions.values():
            target=args if code in {68,70} else args[1] if code==73 else None
            if target is not None and target not in self.actions and target!=len(data):
                raise ValueError("脚本跳转目标无效")


def number(value):
    if value is UNDEFINED: return float('nan')
    if value is None: return 0.
    try: return float(value)
    except (TypeError,ValueError): return float('nan')


def truth(value):
    return False if value is UNDEFINED or isinstance(value,float) and math.isnan(value) else bool(value)


def text(value):
    if value is UNDEFINED:return 'undefined'
    if value is None:return 'null'
    if value is True:return 'true'
    if value is False:return 'false'
    return str(value)


class VM:
    def __init__(self, engine):
        self.engine=engine
        self.calls=0

    def execute(self, program, clip, arguments=(), function=None):
        if self.calls>=32:
            raise ValueError("脚本调用深度超限")
        self.calls+=1
        try: return self.run(program,clip,arguments,function)
        finally:self.calls-=1

    def run(self, program, clip, arguments, function):
        stack=[]; registers={}; local={}; pc=program.start
        if function:
            for index,(register,ref) in enumerate(function.parameters):
                value=arguments[index] if index<len(arguments) else UNDEFINED
                if register:registers[register]=value
                local[program.string(ref)]=value
            # Flash's preload registers, in the order specified by DefineFunction2.
            preload=[(1,clip),(4,list(arguments)),(16,UNDEFINED),(64,self.engine.root),(128,clip.parent or clip),(256,self.engine.globals)]
            at=1
            for flag,value in preload:
                if function.flags&flag: registers[at]=value;at+=1
        local['arguments']=list(arguments)

        def pop(): return stack.pop() if stack else UNDEFINED
        def resolve(value):
            if not isinstance(value,Ref):return value
            return {'this':clip,'root':self.engine.root,'parent':clip.parent or clip,'global':self.engine.globals}.get(value.kind,registers.get(value.value,UNDEFINED))
        def getvar(name):
            if name in local:return local[name]
            return self.engine.variable(clip,name)
        def integer(value):
            value=number(value)
            return int(value) if math.isfinite(value) else 0

        while pc in program.actions:
            self.engine.budget-=1
            if self.engine.budget<0:raise ValueError("脚本单帧执行数量超限")
            code,args,next_pc=program.actions[pc];pc=next_pc
            if code==0:return UNDEFINED
            if code==67:stack.extend(resolve(v) for v in args)
            elif code==1:clip.goto(clip.frame+2,False)
            elif code==2:clip.goto(clip.frame,False)
            elif code in {3,4}:clip.playing=code==3
            elif code in {5,20,22}:
                if code==20:pop()
            elif code==13:pop()
            elif code==14:stack.append(getvar(text(pop())))
            elif code in {15,29}:
                value,name=pop(),text(pop())
                if code==29:local[name]=value
                else:self.engine.set_variable(clip,name,value)
            elif code==34:local[text(pop())]=UNDEFINED
            elif code in {16,46}:
                name=pop();obj=pop()
                if code==16:name=CONSTANTS.get(0x100+integer(name),str(name))
                stack.append(self.engine.get_member(obj,text(name),clip))
            elif code in {17,47}:
                value,name,obj=pop(),pop(),pop()
                if code==17:name=CONSTANTS.get(0x100+integer(name),str(name))
                self.engine.set_member(obj,text(name),value,clip)
            elif code in {30,50,33,51}:
                name=pop();obj=pop() if code in {50,51} else clip
                count=max(0,min(1024,integer(pop()))); params=[pop() for _ in range(count)]
                target=self.engine.get_member(obj,text(name),clip) if code in {50,51} else getvar(text(name))
                if code in {50,51} and (name is UNDEFINED or name is None or name==''):
                    target=obj;obj=clip
                elif code in {30,33} and isinstance(name,(Function,Ref)):
                    target=name
                if target is UNDEFINED:target=Ref('method' if code in {50,51} else 'builtin',text(name))
                stack.append(self.engine.call(target,obj,params,clip,construct=code in {33,51}))
            elif code==31:return pop()
            elif code==35:
                count=max(0,min(4096,integer(pop())));stack.append([pop() for _ in range(count)])
            elif code==36:
                count=max(0,min(4096,integer(pop())));obj={}
                for _ in range(count):value=pop();obj[text(pop())]=value
                stack.append(obj)
            elif code==37:
                v=pop();stack.append('undefined' if v is UNDEFINED else 'boolean' if isinstance(v,bool) else 'number' if isinstance(v,(int,float)) else 'string' if isinstance(v,str) else 'function' if isinstance(v,(Function,Ref)) else 'object')
            elif code==38:stack.append(getattr(pop(),'name',UNDEFINED))
            elif code in {6,39}:
                b,a=pop(),pop();stack.append(text(a)+text(b) if isinstance(a,str) or isinstance(b,str) else number(a)+number(b))
            elif code in {7,8,9,32}:
                b,a=number(pop()),number(pop())
                stack.append(a-b if code==7 else a*b if code==8 else (a/b if b else float('nan')) if code==9 else (a%b if b else float('nan')))
            elif code in {10,11,40,41,60,61}:
                b,a=pop(),pop()
                if code in {10,41}:value=a==b or number(a)==number(b)
                elif code==60:value=type(a) is type(b) and a==b
                else:
                    a,b=(a,b) if isinstance(a,str) and isinstance(b,str) else (number(a),number(b))
                    value=a>b if code==61 else a<b
                stack.append(value)
            elif code==12:stack.append(not truth(pop()))
            elif code==42:stack.append(number(pop()))
            elif code==43:stack.append(text(pop()))
            elif code==44:stack.append(stack[-1] if stack else UNDEFINED)
            elif code==45:
                if len(stack)>=2:stack[-2:]=stack[-2:][::-1]
            elif code in {48,49}:stack.append(number(pop())+(1 if code==48 else -1))
            elif code in {54,55,56,57,58,59}:
                b,a=integer(pop()),integer(pop())
                stack.append(a&b if code==54 else a|b if code==55 else a^b if code==56 else (a<< (b&31)) if code==57 else ((a&0xffffffff)>>(b&31)) if code==59 else a>>(b&31))
            elif code in {63,74}:
                value=stack[-1] if code==63 and stack else pop()
                for register in args if isinstance(args,list) else [args]:registers[register]=value
            elif code==75:
                for register in args:registers[register]=UNDEFINED
            elif code==64:
                value=Function(args.name,args.flags,args.parameters,args.program,clip)
                if value.name:clip.variables[value.name]=value
                else:stack.append(value)
            elif code==68:pc=args
            elif code in {70,73}:
                kind,target=(1000,args) if code==70 else args
                b=pop();a=pop() if kind in set(range(6))|{7,8,9,10} else UNDEFINED
                if kind==1000:value=truth(b)
                elif kind==6:value=not truth(b)
                elif kind in {11,12}:value=(b is UNDEFINED)==(kind==11)
                elif kind in {0,1,9,10}:value=(type(a) is type(b) and a==b) if kind>=9 else (a==b or number(a)==number(b));value=value if kind in {0,9} else not value
                elif kind in {7,8}:value=bool(integer(a)&integer(b));value=value if kind==7 else not value
                else:
                    a,b=number(a),number(b);value={2:operator.lt,3:operator.gt,4:operator.le,5:operator.ge}.get(kind,lambda a,b:False)(a,b)
                if value:pc=target
            elif code==71:
                stop,additional=args;target=pop()
                if isinstance(target,(int,float)):target=number(target)+additional
                clip.goto(target,not stop)
            elif code==72:stack.append(self.engine.target(clip,pop()))
            elif code==76:registers[args[0]]=number(registers.get(args[0],UNDEFINED))+args[1]
            elif code==77:
                name=text(pop());self.engine.set_variable(clip,name,number(getvar(name))+args)
            elif code==26:stack.append(self.engine.tick*1000/60)
            elif code in {27,28}:
                name=text(pop());obj=pop() if code==27 else clip
                self.engine.delete_member(obj,name);stack.append(True)
            elif code==19:self.engine.remove(self.engine.target(clip,pop()))
            elif code==18:
                depth,name,target=integer(pop()),text(pop()),self.engine.target(clip,pop())
                self.engine.clone(target,name,depth)
            elif code==53:
                obj=pop();stack.append(None)
                stack.extend(self.engine.member_names(obj))
            elif code in {24,52}:
                cls,obj=pop(),pop();stack.append(False if code==52 else None)
            elif code in {25,62}:pop();pop()
            elif code in {21,69}:
                self.engine.issues.add("未执行交互或外部访问脚本")
                pop();pop()
            elif code==66:raise ValueError("脚本 WITH 作用域尚未实现")
            elif code==23:raise ValueError("素材脚本抛出异常："+text(pop()))
            else:raise ValueError(f"未实现的脚本指令：0x{code:02x}")
            if len(stack)>8192:raise ValueError("脚本栈大小超限")
        return UNDEFINED
