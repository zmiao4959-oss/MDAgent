from dataclasses import dataclass,field

from distro import name
from miniclaw.settings import config
from ..memory.session import Session,SessionManager
from ..agent import Agent,AgentContext
from ..llm.router import LLMRouter
from ..agents.profile import AgentProfile
from miniclaw.agents import AgentCatalog
import asyncio
@dataclass
class Member:
    single_agent:Agent
    chat_id:str
    inbox:list=field(default_factory=list)
    is_running:bool=False 
    loop_times:int=0
    
class RoomBinding:
    def __init__(self,room,member:Member):
        self.room = room
        self.member = member
    async def execute_tool(self,name:str,params:dict)->str:
        if name == "publish_message":
            if not isinstance(params,dict):
                return "Error: Params must be a dictionary"
            if set(params)!={"message"}:
                return "Error: Params must contain only 'message' key"   
            message = params.get("message")
            if not isinstance(message,str) or not message.strip():
                return "Error: 'message' must be a non-empty string"
            try:
                self.room.publish_message(self.member,message)
                return f"Message published: {message}"
            except Exception as e:
                return f"Error occurred while publishing message: {e}"
        else:return "Error: Unknown tool name"

class room:
    def __init__(self,
                 profiles:list[str],
                 room_id:str,
        ):
        
        router = LLMRouter(config.llm, config.llm.fallback_providers)
        session_mgr = SessionManager(config.paths.sessions)
        agent_catalog = AgentCatalog()
        profiles = [agent_catalog.require(profile) for profile in profiles]
        self.members = [Member(single_agent=Agent(router,session_mgr,profile),chat_id=f"room:{room_id}:{profile.name}") for profile in profiles]
        self.public_message = list()
    def broadcast(self,member,message):
        for mem in self.members:
            if mem == member:continue
            mem.inbox.append(message)
    def publish_message(self,mem:Member,message:str):
        self.public_message.append(message)
        self.broadcast(mem,message)

    async def member_loop(self):
        for mem in self.members:
            if mem.is_running or not mem.inbox:continue
            message = "\n".join(mem.inbox)
            mem.inbox = []
            chat_id = mem.chat_id
            channel = "agent_room"
            account_id = "system"
            user_message = message
            mem.is_running = True
            try:
                binding = RoomBinding(self,mem)
                res = await mem.single_agent.process_message(
                    AgentContext(
                        chat_id,
                        channel,
                        account_id,
                        user_message,
                        metadata={"_graph_binding":binding}))
            finally:
                mem.is_running = False

            
    



            



