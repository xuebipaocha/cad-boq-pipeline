"""
审图规则引擎 - 基类
每条规则继承 RuleBase，实现 check() 方法
"""
from abc import ABC, abstractmethod

class RuleBase(ABC):
    def __init__(self):
        self.name = self.__class__.__name__
        self.description = ''
    
    @abstractmethod
    def check(self, drawing_data):
        """
        检查图纸数据，返回问题列表
        drawing_data: 识图结果.json 的 dict
        返回: [{"问题":str, "位置":str, "类别":str, "影响造价":bool, "严重程度":str, "建议":str}]
        """
        pass
