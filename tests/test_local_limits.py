import pytest
from kitt.domain.entities import ModelProfile
from kitt.core.turn_processor import TurnProcessor, detect_chat_limit_message
from kitt.router.router import TaskRouter


def test_detect_chat_limit_message_english():
    assert detect_chat_limit_message("You've reached your message limit. Try again in 2 hours.") is not None
    assert detect_chat_limit_message("You have reached the daily rate limit") is not None
    assert detect_chat_limit_message("Too many requests, please try again") is not None
    assert detect_chat_limit_message("session limit exceeded") is not None
    assert detect_chat_limit_message("quota limit exceeded") is not None
    assert detect_chat_limit_message("Here is the updated function: def foo(): pass") is None


def test_detect_chat_limit_message_portuguese():
    assert detect_chat_limit_message("Você atingiu o limite de mensagens. Tente novamente mais tarde.") is not None
    assert detect_chat_limit_message("Limite de uso excedido. Tente novamente em 15 minutos.") is not None
    assert detect_chat_limit_message("Muitas requisições, tente novamente") is not None
    assert detect_chat_limit_message("Arquivo modificado com sucesso.") is None


def test_turn_processor_local_limits_enforced(tmp_path):
    # Default: enforce_local_limits=True
    profile = ModelProfile(
        backend="ollama",
        model="qwen2.5:7b",
        enforce_local_limits=True,
    )
    assert profile.enforce_local_limits is True

    # Check router serialization preserves enforce_local_limits
    router = TaskRouter(root_dir=str(tmp_path))
    router.config.profiles["test_prof"] = profile
    router.save_config(str(tmp_path))

    loaded_router = TaskRouter(root_dir=str(tmp_path))
    assert loaded_router.config.profiles["test_prof"].enforce_local_limits is True


def test_turn_processor_local_limits_bypassed(tmp_path):
    # When enforce_local_limits=False
    profile = ModelProfile(
        backend="kitt-reverse-proxy",
        model="gpt-4o",
        enforce_local_limits=False,
    )
    assert profile.enforce_local_limits is False

    router = TaskRouter(root_dir=str(tmp_path))
    router.config.profiles["proxy_prof"] = profile
    router.save_config(str(tmp_path))

    loaded_router = TaskRouter(root_dir=str(tmp_path))
    assert loaded_router.config.profiles["proxy_prof"].enforce_local_limits is False


@pytest.mark.asyncio
async def test_handle_local_limits_command(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from kitt.ui.model_commands import handle_local_limits_command
    from kitt.router.router import TaskRouter

    router = TaskRouter(root_dir=str(tmp_path))
    profile = ModelProfile(
        backend="kitt-reverse-proxy",
        model="gpt-4o",
        enforce_local_limits=False,
    )
    router.config.profiles["execute"] = profile

    app = MagicMock()
    app.runtime.processor.router = router
    app.state.workspace_path = str(tmp_path)
    app._run_blocking = AsyncMock(side_effect=lambda fn, *args: fn(*args))
    app._ensure_daemon_management = AsyncMock(return_value=False)
    app._role_tasks = lambda role: ("execute", ["code-generation"])

    # Test toggling / setting to on
    await handle_local_limits_command(app, "execute on")
    assert router.config.profiles["execute"].enforce_local_limits is True

    # Test toggling / setting to off
    await handle_local_limits_command(app, "execute off")
    assert router.config.profiles["execute"].enforce_local_limits is False

    # Test toggle without on/off
    await handle_local_limits_command(app, "execute")
    assert router.config.profiles["execute"].enforce_local_limits is True
