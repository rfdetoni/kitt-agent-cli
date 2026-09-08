# Interfaces Web KITT — revisão de 8 de setembro de 2026

Esta revisão abrange o Agent Web, no repositório `kitt-agent-cli`, e o
configurador Control Center, no repositório `kitt-assistant`.

## Identidade visual e abordagem

O tema mantém a referência ao KITT/Knight Industries da fase de 2008: superfícies
escuras, instrumentos compactos, detalhes vermelhos e o scanner do configurador.
O vermelho fica concentrado na identidade, seleção e ações principais. Bordas,
tipografia do sistema e espaços menores substituem parte dos brilhos, sombras e
desfoques. Não foram adicionadas dependências de produção, fontes remotas,
frameworks de UI, bibliotecas de edição ou chamadas a serviços externos.

## Agent Web

- Cabeçalho compacto e organização de workspace, com sessões, conversa central
  e inspetor de eventos, ferramentas, aprovações, diff, artefatos e status.
- Sessões e inspetor podem ser ocultados no desktop, liberando efetivamente
  espaço para a conversa. No celular, abrem como painéis independentes; Escape
  fecha o painel e devolve o foco à mensagem.
- Divisórias redimensionáveis por arraste ou pelas setas do teclado. Home ou
  duplo clique restaura a largura padrão; o botão de layout restaura os painéis.
  Larguras são limitadas à viewport e persistidas apenas no navegador.
- Conversa com leitura mais confortável, menos caixas e ferramentas recolhíveis
  por meio de `details`/`summary` nativos.
- Blocos de código cercados por três crases têm área monoespaçada, rolagem
  horizontal e botão de cópia. O restante continua sendo texto, sem interpretar
  HTML ou executar conteúdo enviado por modelos e ferramentas.
- Streaming agrupa atualizações de texto por frame e deixa de reconstruir o
  inspetor para cada `TextDelta`. Deltas continuam na conversa, mas não inundam
  a lista de eventos recebidos ao vivo.
- Rolagem automática respeita quem voltou para ler mensagens anteriores. O botão
  “Ir ao fim” retoma o acompanhamento. Enviar uma mensagem também retoma esse modo.
- Troca de sessão limpa o estado visual de cancelamento e ignora respostas
  atrasadas de detalhes/artefatos de outra sessão.
- Abas com estados ARIA, navegação por setas/Home/End, foco visível, rótulos de
  entrada e link para pular à mensagem. O workspace fica inerte durante pareamento.
- Enter respeita composição de texto por IME. Movimento reduzido é respeitado.

Arquivos: `kitt/remote/static/index.html`, `app.css`, `app.js` e
`tests/web_ui.browser.mjs`.

## Configurador Control Center

- Navegação lateral redimensionável com persistência local, teclado, duplo clique
  e botão para restaurar o layout.
- Barra de ações fixa no desktop, título da seção atual, resumo mais compacto e
  barra inferior com quantidade de alterações pendentes.
- Grids adaptativos e redução do cabeçalho no celular. O seletor móvel continua
  permitindo acesso a todas as seções. O resumo redundante fica oculto na tela
  pequena de configuração, preservando as métricas do monitor.
- Logs e revisão de diff podem ser redimensionados verticalmente. Atualizações
  do monitor preservam altura e posição de leitura do painel de logs.
- Campos têm rótulos associados e indicação visual de alteração. Repor o valor
  original remove a alteração pendente.
- Editar e sair de um campo não reconstrói mais toda a interface: foco e cliques
  no próximo controle são preservados.
- Revisão continua obrigatória antes da aplicação. A validação nativa verifica
  campos visíveis antes de pedir o diff ao servidor; números não são truncados
  silenciosamente por `parseInt`.
- Ctrl+K/Cmd+K foca a busca. Buscar pelo monitor abre os resultados de configuração.
- Aviso nativo ao sair da página com alterações pendentes; nenhuma configuração
  ou credencial é armazenada em localStorage.

Arquivos no `kitt-assistant`: `apps/kittd/control-center-web/index.html`,
`app.css`, `app.js` e `tests/control_center_ui.browser.mjs`.

## Validação executada

- Agent CLI: 791 testes Python e 17 subtestes; inclui 39 testes específicos de
  Web/Remote e comandos relacionados.
- Agent CLI: compileall, guarda de packaging clean-room e 2 testes Rust passaram.
- Configurador: 12 testes JavaScript existentes e 42 testes Rust passaram.
- Dois testes de fluxo completo no Chromium passaram com os assets reais e APIs
  simuladas. Cobrem arraste, teclado, persistência, ocultação de painéis,
  streaming sem roubar rolagem, texto de código seguro, foco entre campos,
  revisão antes de gravar, navegação móvel e preservação do painel de logs.
- Viewports verificadas: desktop de 1440 px; 1024, 960 (configurador), 768,
  390 e 320 px. Capturas de desktop/celular também foram inspecionadas.
- Build release do `kittd` atualizado. O serviço já configurado nesta máquina
  aponta para esse binário. O Agent CLI usa instalação editável deste checkout.

Os testes de navegador não acionaram modelos, aprovações reais, reinícios de
serviços ou alterações nas configurações pessoais. Não foram executados testes
nativos de Windows/macOS nem uma auditoria automatizada completa de acessibilidade.

## Segurança e desempenho

| Severidade / confiança | Local | Problema corrigido e verificação |
| --- | --- | --- |
| Média / alta | Agent `app.js` | A atualização por token reconstruía o inspetor e forçava a rolagem. Atualização de texto agrupada e acompanhamento opcional, verificados com 50 deltas e leitura fora do fim. |
| Média / alta | Control Center `app.js` | O rerender no blur eliminava o próximo alvo de foco/clique. Atualização pontual dos campos, verificada editando, mudando foco e abrindo revisão com um clique. |
| Baixa / alta | CSS e layout JS das duas telas | Larguras rígidas e controles sem alternativa de teclado. Divisórias limitadas, estados ARIA e testes de viewport/teclado. |

A formatação de código usa nós de texto, não HTML do modelo. Os caminhos de
autenticação, CSRF, validação do servidor, aprovações e contenção de arquivos não
foram relaxados. Preferências de largura são números finitos com limites e
localStorage indisponível não bloqueia a inicialização. Pointer capture é liberado
pelo navegador ao encerrar/cancelar o gesto. A alteração melhora trabalho de
renderização e custo visual; não representa uma medição de redução do bundle.

## Iniciar nesta máquina

Agent Web, usando o workspace desejado:

```bash
rtk proxy kitt web --root /home/toni/otherProjects/kitt --host 127.0.0.1 --port 7337
```

Abra `http://127.0.0.1:7337` e informe o código de pareamento exibido no terminal.
Ctrl+C encerra o gateway web e mantém o daemon persistente do agente.

Configurador, carregando o binário recompilado pelo serviço existente:

```bash
rtk proxy systemctl --user restart kitt-assistant.service
rtk proxy systemctl --user status kitt-assistant.service --no-pager
```

Abra `http://127.0.0.1:41828`. O restart também reinicia o assistant, pois o
configurador está embutido no `kittd`. A interface nova entra em uso após esse
comando; o serviço em execução não é atualizado apenas pelo build.

## Repetir testes de navegador

No diretório `/home/toni/otherProjects/kitt`, reutilizando o Playwright já
instalado no reverse proxy:

```bash
rtk proxy env PLAYWRIGHT_MODULE=/home/toni/otherProjects/kitt/kitt-reverse-proxy/node_modules/playwright/index.mjs node --test kitt-agent-cli/tests/web_ui.browser.mjs
rtk proxy env PLAYWRIGHT_MODULE=/home/toni/otherProjects/kitt/kitt-reverse-proxy/node_modules/playwright/index.mjs node --test kitt-assistant/tests/control_center_ui.browser.mjs
```

Em outra máquina, `PLAYWRIGHT_MODULE` pode apontar para uma instalação existente
de Playwright com Chromium disponível. Sem a variável, os testes procuram o
pacote `playwright` pela resolução normal do Node. Playwright não é dependência
de produção das interfaces.
