"""Base provider interface and registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional, Type


@dataclass
class ProviderInfo:
    """Metadata about a sandbox provider."""
    
    name: str
    description: str
    docs_url: str
    pricing_url: Optional[str] = None
    mcp_server: bool = False
    openapi_spec: bool = False
    llms_txt: bool = False


class SandboxProvider(ABC):
    """Base class for sandbox providers."""

    name: str = "base"
    info: ProviderInfo = ProviderInfo(
        name="base",
        description="Base provider",
        docs_url="",
    )
    _api_call_count: int = 0

    @property
    def api_calls(self) -> int:
        """Number of actual API/network calls made by this provider."""
        return self._api_call_count

    def reset_api_calls(self) -> None:
        """Reset the API call counter."""
        self._api_call_count = 0

    def _count_api_call(self, n: int = 1) -> None:
        """Increment the API call counter."""
        self._api_call_count += n
    
    @abstractmethod
    async def authenticate(self, api_key: str) -> None:
        """
        Authenticate with the provider.
        
        Args:
            api_key: The API key or token for authentication
            
        Raises:
            AuthenticationError: If authentication fails
        """
        pass
    
    @abstractmethod
    async def create_sandbox(
        self,
        image: Optional[str] = None,
        timeout_seconds: int = 300,
    ) -> str:
        """
        Create a new sandbox environment.
        
        Args:
            image: Optional base image/template
            timeout_seconds: Auto-destroy after this many seconds
            
        Returns:
            Sandbox ID
            
        Raises:
            SandboxCreationError: If creation fails
        """
        pass
    
    @abstractmethod
    async def execute(
        self,
        sandbox_id: str,
        code: str,
        language: str = "python",
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """
        Execute code in the sandbox.
        
        Args:
            sandbox_id: The sandbox to execute in
            code: Code to execute
            language: Programming language
            timeout_seconds: Execution timeout
            
        Returns:
            Tuple of (stdout, stderr, exit_code)
            
        Raises:
            ExecutionError: If execution fails
        """
        pass
    
    @abstractmethod
    async def write_file(
        self,
        sandbox_id: str,
        path: str,
        content: str | bytes,
    ) -> None:
        """
        Write a file to the sandbox.
        
        Args:
            sandbox_id: The sandbox to write to
            path: File path within the sandbox
            content: File content (str or bytes)
            
        Raises:
            FileOperationError: If write fails
        """
        pass
    
    @abstractmethod
    async def read_file(
        self,
        sandbox_id: str,
        path: str,
    ) -> str | bytes:
        """
        Read a file from the sandbox.
        
        Args:
            sandbox_id: The sandbox to read from
            path: File path within the sandbox
            
        Returns:
            File content
            
        Raises:
            FileOperationError: If read fails
        """
        pass
    
    @abstractmethod
    async def destroy(self, sandbox_id: str) -> None:
        """
        Destroy a sandbox.
        
        Args:
            sandbox_id: The sandbox to destroy
        """
        pass
    
    async def execute_command(
        self,
        sandbox_id: str,
        command: str,
        timeout_seconds: int = 30,
    ) -> tuple[str, str, int]:
        """
        Execute a shell command in the sandbox.

        Default implementation delegates to execute() with language="sh".
        Providers may override for more efficient shell execution.

        Args:
            sandbox_id: The sandbox to execute in
            command: Shell command to run
            timeout_seconds: Execution timeout

        Returns:
            Tuple of (stdout, stderr, exit_code)
        """
        return await self.execute(
            sandbox_id, command, language="sh", timeout_seconds=timeout_seconds
        )

    async def get_status(self, sandbox_id: str) -> str:
        """
        Get sandbox status.
        
        Returns:
            Status string (running, stopped, error, etc.)
        """
        return "unknown"
    
    def get_discoverability_score(self) -> float:
        """
        Calculate discoverability score based on provider info.
        
        Returns:
            Score from 1.0 to 5.0
        """
        score = 3.0  # Base score for having docs
        
        if self.info.mcp_server:
            score += 1.0
        if self.info.openapi_spec:
            score += 0.5
        if self.info.llms_txt:
            score += 0.5
        
        return min(5.0, score)


# Provider registry
_providers: Dict[str, Type[SandboxProvider]] = {}


def register_provider(provider_class: Type[SandboxProvider]) -> None:
    """Register a provider class."""
    _providers[provider_class.name] = provider_class


def get_provider(name: str) -> Type[SandboxProvider]:
    """Get a provider class by name."""
    if name not in _providers:
        raise ValueError(f"Unknown provider: {name}. Available: {list(_providers.keys())}")
    return _providers[name]


def list_providers() -> list[str]:
    """List all registered provider names."""
    return list(_providers.keys())
