import json
from pathlib import Path
from typing import Optional, Dict, Any

class PromptLoader:
    _instance = None
    _prompts: Dict[str, str] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(PromptLoader, cls).__new__(cls)
            cls._instance._load_prompts()
        return cls._instance
    
    def _load_prompts(self):
        prompt_dir = Path(__file__).parent
        for prompt_file in prompt_dir.glob("*.md"):
            prompt_name = prompt_file.stem
            try:
                with open(prompt_file, 'r', encoding='utf-8') as f:
                    self._prompts[prompt_name] = f.read()
            except Exception as e:
                print(f"Error loading prompt {prompt_name}: {e}")
    
    def get_prompt(self, name: str) -> Optional[str]:
        return self._prompts.get(name)
    
    def format_prompt(self, name: str, **kwargs) -> Optional[str]:
        prompt = self.get_prompt(name)
        if prompt is None:
            return None
        try:
            return prompt.format(**kwargs)
        except KeyError as e:
            print(f"Missing required argument for prompt {name}: {e}")
            return None
    
    def reload(self):
        self._prompts.clear()
        self._load_prompts()
    
    def list_prompts(self) -> list:
        return list(self._prompts.keys())

def load_prompt(name: str) -> Optional[str]:
    return PromptLoader().get_prompt(name)

def format_prompt(name: str, **kwargs) -> Optional[str]:
    return PromptLoader().format_prompt(name, **kwargs)