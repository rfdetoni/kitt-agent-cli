# K.I.T.T. Agent CLI

[English](README.md)

**Control plane local-first para agentes autônomos de programação.**

O K.I.T.T. Agent CLI reúne inteligência de repositório, roteamento de providers/modelos, goals, subagentes, memória, políticas, aprovações, plugins, MCP e execução segura por meio do `kitt_runtime`.

A implementação principal permanece em Python. Trabalho determinístico e pesado pode usar a aceleração opcional `kitt_native`, mantida pelo `kitt-toolbox`, sem alterar a API apresentada ao modelo.

## Comece aqui

- [QUICKSTART.md](QUICKSTART.md) — instalação e primeiro uso
- [CONTRIBUTING.md](CONTRIBUTING.md) — contribuição e gates
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — arquitetura do Agent
- [docs/DOMAIN_ARCHITECTURE.md](docs/DOMAIN_ARCHITECTURE.md) — bounded contexts do ecossistema
- [docs/RUNTIME_REFERENCE.md](docs/RUNTIME_REFERENCE.md) — referência do runtime
- [docs/ACCESSIBILITY.md](docs/ACCESSIBILITY.md) — teclado, cores e alto contraste
- [docs/PERFORMANCE.md](docs/PERFORMANCE.md) — benchmarks reproduzíveis

## Requisitos

- Python 3.14+
- Git
- Rust/Cargo é opcional para uso standalone do Agent; o backend Python continua disponível

Para a instalação completa do ecossistema, prefira `rfdetoni/kitt`.

## Instalação

Linux/macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/rfdetoni/kitt-agent-cli/main/install.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/rfdetoni/kitt-agent-cli/main/install.ps1 | iex
```

## Uso básico

```bash
kitt
kitt --root /caminho/do/projeto
kitt models
kitt sessions
kitt doctor
kitt --help
```

Na TUI, `Ctrl+P` abre a descoberta de comandos, `F12` abre a configuração de provider/modelo e `F10` alterna o suporte a mouse.

## Arquitetura do ecossistema

O ecossistema usa bounded contexts por responsabilidade:

- **Agent CLI:** orquestração e política de execução;
- **Protocol:** contratos compartilhados;
- **Memory:** memória semântica persistente;
- **Toolbox:** aceleração nativa e data plane;
- **Assistant:** daemon e control center residente;
- **AI Workers:** evals/evolução e workers isolados;
- **Reverse Proxy:** gateway autorizado para providers/API/web.

As fronteiras são mais importantes que a adoção nominal de DDD. Conceitos de domínio ficam independentes de UI, provider e persistência; detalhes de infraestrutura permanecem nos adaptadores que os possuem.

## Acessibilidade

```bash
NO_COLOR=1 kitt
KITT_HIGH_CONTRAST=1 kitt
```

A navegação por teclado continua sendo a referência funcional. Ações de mouse devem ter equivalentes por teclado.

## Desenvolvimento

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m compileall -q kitt tests
python packaging/verify_cleanroom.py
```

## Licença

MIT. Consulte [LICENSE](LICENSE).

> O README em inglês é a fonte canônica para detalhes extensos e notas históricas. Este documento mantém o caminho de adoção e as decisões arquiteturais essenciais em português.
