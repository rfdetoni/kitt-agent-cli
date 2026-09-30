# K.I.T.T. Agent CLI

[English](README.md)

**Control plane local-first para agentes autônomos de programação.**

## Agent CLI 0.80.1 — roles estruturais, processos gerenciados e aprendizado mensurável

O Agent agora aplica estruturalmente os roles `DISCOVER`, `ARCHITECT`, `IMPLEMENT`, `VERIFY` e `REVIEW`, com tools/capabilities, mutação, contexto, modelo e orçamento próprios. O boundary de execução revalida a policy do role; persona de prompt não é mecanismo de segurança.

Processos longos podem usar `process.start/read/stdin/signal/stop/resume`. Saída e término viram eventos duráveis `PROCESS_OUTPUT`/`PROCESS_EXIT`, o buffer é limitado/redigido e operações de controle reutilizam a identidade do processo mas revalidam o `ExecutionAuthoritySnapshot` capturado no início.

`kitt learn` analisa telemetria local com assinaturas anonimizadas, detecta desperdícios como rereads/retries/compaction/context duplication e permite janelas `control/candidate` com `experiment start/switch/report`. O sistema não registra argumentos sensíveis nessa visão e nunca promove candidato automaticamente apenas por parecer menor.

A descoberta de skills recebe limites de roots/profundidade/arquivos/bytes antes da seleção semântica. Hooks públicos de lifecycle enviam somente digests de evidência para o mesmo pipeline de jobs do `kitt-memoryd`, preservando o Memory como única autoridade de memória durável.

Compatibilidade desta rodada: KITT Protocol **0.5.1**, KITT Memory **0.6.1**, Reverse Proxy **4.7.1** e Assistant Runtime **0.2.26**.

## Agent CLI 0.79.0 — loops de execução LLM-first

A execução via Reverse Proxy/WebChat agora usa o agent-contract **v2** com uma rota neutra `agent-loop`. O pedido original do usuário é encaminhado sem reescrita e passa a ser a autoridade semântica: o Agent CLI não compila, traduz, resume nem classifica lexicalmente a linguagem natural antes de o WebChat decidir a próxima ação.

O KITT continua determinístico nas responsabilidades corretas: capabilities, policy/approvals, execução de repositório/processos, evidência do host, cancelamento, limites de tokens e gates de validação. O modelo mantém um loop curto com objetivo, critérios de conclusão e status. Após `agent_loop_action_budget` round trips do host (padrão **4**), o Reverse Proxy exige um checkpoint para que o modelo reavalie a evidência real antes de continuar.

A primeira mutação do `agent-loop` exige evidência do repositório e, após qualquer mutação, a conclusão continua bloqueada até uma validação host bem-sucedida quando houver `process.run`. Esta é uma mudança interna do ecossistema: Agent CLI 0.79.0 deve ser usado com Reverse Proxy 4.7.0 e não mantém compatibilidade com agent-contract v1.

## Agent CLI 0.78.11 — cópia no transcript e continuidade WebChat

O transcript principal voltou a permitir seleção e cópia nativas pelo terminal sem desativar o mouse dos menus e modais. O aviso de `Ctrl+O` agora aparece somente no último bloco de tool/thought que o atalho realmente consegue expandir ou recolher.

Em conjunto com o KITT Reverse Proxy 4.6.8, sessões WebChat novas também recebem o contexto anterior da conversa da API uma única vez, sem reenvio crescente do histórico nos turnos seguintes.

## Agent CLI 0.78.10 — dependências K.I.T.T. seguindo main

Dependências VCS internas do ecossistema agora seguem `main` dos repositórios irmãos em vez de embutir SHAs cross-repo. O instalador raiz compõe os sources atuais e registra os SHAs efetivamente instalados apenas como proveniência.

## Agent CLI 0.78.9 — recuperação após Ctrl+C

O cancelamento agora cria uma fronteira real para a fila local do TUI. Se uma thread de provider/tool ainda estiver encerrando após **Ctrl+C**, o bridge aposenta aquele executor de worker único e permite que o próximo prompt comece em um worker novo, em vez de ficar preso atrás do turno cancelado. A limpeza por geração também impede que um consumidor antigo apague o estado do turno substituto.

Agent CLI 0.78.8 também deixou de encerrar automaticamente a conversa quando o modelo/chat responde fora do contrato. Quando o Reverse Proxy sinaliza uma falha recuperável, a sessão é preservada e o TUI apresenta **Continuar** e **Tentar novamente**, também clicáveis por mouse.

A retomada usa a mesma conversa e orienta o modelo a não repetir tools ou mutações já concluídas. Erros reais de rede/protocolo que não forem marcados como recuperáveis continuam seguindo o fluxo normal de falha.

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
- [docs/FIGMA.md](docs/FIGMA.md) — integração Figma via MCP oficial

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
kitt learn
kitt learn suggest
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
