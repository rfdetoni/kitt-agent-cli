# Dicas — Bubblewrap, AppArmor e \`uid_map\` no Linux

Este guia trata falhas do **sandbox de processos do KITT Agent CLI** no Linux.
Não se aplica automaticamente ao processo que roda **dentro de uma VM convidada**:
diagnostique o sistema onde o \`process.run\` realmente é executado.

## Sintomas

- \`Strong sandbox profile 'workspace-write' requires functional bubblewrap >= 0.12\`
- \`bwrap: setting up uid map: Permission denied\`
- \`apparmor="DENIED" ... profile="unprivileged_userns" ... name="proc/<pid>/uid_map"\`
- \`apparmor="DENIED" ... comm="bwrap" ... capname="setpcap"\`

A mensagem sobre \`setpcap\` sozinha **não** basta para identificar a causa.
O conjunto \`userns_create\` + \`uid_map\` negado durante a execução do
\`bwrap\` é evidência muito mais específica de um bloqueio do AppArmor.
Mensagens \`DENIED\` de outros processos (por exemplo, \`fusermount3\`) não
devem ser atribuídas ao KITT sem correlação por processo/horário.

## 1. Identifique o binário e as restrições

Execute **no host do Agent CLI**, como seu usuário normal:

\`\`\`bash
type -a bwrap
/usr/local/bin/bwrap --version 2>/dev/null || true
/usr/bin/bwrap --version 2>/dev/null || true

sysctl kernel.unprivileged_userns_clone
sysctl kernel.apparmor_restrict_unprivileged_userns
sysctl user.max_user_namespaces
sudo aa-status | grep -Ei 'bwrap|userns' || true
ls /etc/apparmor.d/*bwrap* 2>/dev/null || true

sudo journalctl -k --since "15 minutes ago" --no-pager |
  grep -Ei 'apparmor|userns|uid_map|bwrap|denied'
\`\`\`

Interpretação:

- \`kernel.unprivileged_userns_clone=1\` indica que o kernel permite
  namespaces de usuário não privilegiados **em princípio**.
- \`kernel.apparmor_restrict_unprivileged_userns=1\` indica que o Ubuntu pode
  exigir uma regra/perfil AppArmor **para o executável específico**.
- \`user.max_user_namespaces\` precisa ser maior que zero.
- A versão do \`bwrap\` **não prova funcionamento**: um binário >= 0.12.0 pode
  falhar na criação de namespaces por causa do AppArmor.
- Verifique \`execpath\` nos logs. \`/usr/local/bin/bwrap\`,
  \`/usr/bin/bwrap\` e \`~/bubblewrap/build/bwrap\` são **caminhos diferentes**
  para o AppArmor; autorizar um deles não autoriza automaticamente os demais.

## 2. Reproduza o teste funcional do KITT

Ajuste \`BWRAP\` para **o executável realmente usado**, sem \`sudo\`:

\`\`\`bash
BWRAP=/usr/local/bin/bwrap
"$BWRAP" --version
"$BWRAP" \
  --die-with-parent \
  --new-session \
  --unshare-user \
  --unshare-pid \
  --unshare-ipc \
  --unshare-uts \
  --ro-bind / / \
  --proc /proc \
  --dev /dev \
  -- /usr/bin/true
echo "Exit code: $?"
\`\`\`

\`Exit code: 0\` indica que **esse teste de namespaces** passou.
Não significa que a política de execução completa do workspace tenha sido
validada ou que o KITT já esteja usando esse caminho.

## 3. Corrija o AppArmor sem desabilitar a proteção global

1. **Prefira o perfil da distribuição**, se disponível. Inspecione antes de
   alterar:

   \`\`\`bash
   sudo apt install apparmor-utils apparmor-profiles
   sudo find /etc/apparmor.d /usr/share/apparmor \
     -iname '*bwrap*' -o -iname '*bubblewrap*'
   \`\`\`

2. Se houver um \`bwrap-userns-restrict\` compatível com **sua versão do
   AppArmor**, prefira esse modelo: ele permite ao \`bwrap\` configurar os
   namespaces necessários, mas restringe privilégios adicionais nos processos
   dentro do sandbox. Faça a revisão do conteúdo e ajuste a **regra de
   anexação para o caminho real** (por exemplo, \`/usr/local/bin/bwrap\`).
   Não copie um perfil de outra versão do AppArmor sem conferir o \`abi\` e
   as regras que essa distribuição suporta.
3. Após instalar ou modificar o perfil revisado em \`/etc/apparmor.d/\`,
   valide, recarregue e confirme que ele está ativo:

   \`\`\`bash
   sudo apparmor_parser -Q /etc/apparmor.d/NOME_DO_PERFIL
   sudo apparmor_parser -r /etc/apparmor.d/NOME_DO_PERFIL
   sudo aa-status | grep -Ei 'bwrap|userns'
   \`\`\`

4. Repita **o mesmo teste da seção 2**. Se falhar, consulte as novas linhas
   \`DENIED\` e verifique \`execpath\`, \`profile\`, \`name\` e \`pid\` antes de
   ajustar qualquer outra permissão.

Um perfil com \`flags=(unconfined)\` e \`userns,\` para um caminho específico
é uma alternativa **menos restritiva**, não a opção recomendada. Exige
avaliação explícita do administrador, especialmente em hosts multiusuário:
o perfil limitado ao caminho ainda reduz o confinamento AppArmor do binário.
Não aplique modelos copiados da internet sem revisar as permissões e o
efeito sobre processos filhos.

**Não faça** os seguintes atalhos:

- Desativar \`kernel.apparmor_restrict_unprivileged_userns\` globalmente.
- Aplicar \`chmod u+s\` ao \`bwrap\` compilado.
- Desativar o AppArmor ou configurar um bypass global de \`uid_map\`.
- Rodar o KITT com \`sudo\` para contornar o problema.
- Trocar \`process.run\` por um shell não isolado quando o host exigir
  sandbox forte.

## 4. Confira novamente o Agent CLI

A partir do **Agent CLI 0.88.35**, o KITT examina automaticamente
\`/usr/local/bin/bwrap\`, \`/usr/bin/bwrap\`, \`/bin/bwrap\` e os diretórios
de \`PATH\`. Ele ignora versões inferiores a **0.12.0**, testa os namespaces
de cada candidato e escolhe o primeiro funcional. Binários dentro do
workspace não são aceitos como host sandbox. O diagnóstico e o resultado
são atualizados periodicamente (intervalo de **60 segundos**).

Consulte a operação \`kitt_runtime\` → \`runtime.capabilities\` no Agent
ou as mensagens de diagnóstico de \`process.run\`:

- \`workspace_process_sandbox.available=true\`: um binário seguro passou
  no teste de detecção do host.
- \`available=false\`: o campo \`diagnostic\` explica os candidatos
  rejeitados (versão, erro de namespaces ou AppArmor).

Se o teste manual passar mas o KITT ainda falhar, confira a **versão
instalada do Agent CLI**, o \`PATH\` do serviço que iniciou o KITT e o
binário exato listado pelo diagnóstico. Uma sessão/serviço mais antigo pode
continuar com o executável ou com o código anterior à atualização.

## Referências

- [AppArmor upstream — exemplo de perfil restritivo \`bwrap-userns-restrict\`](https://gitlab.com/apparmor/apparmor/-/blob/master/profiles/apparmor/profiles/extras/bwrap-userns-restrict)
- [Ubuntu/AppArmor — discussão sobre perfil que não é anexado ao binário](https://lists.ubuntu.com/archives/apparmor/2024-November/013408.html)
- [Bubblewrap no Ubuntu 24.04 — explicação de perfis e alternativas](https://github.com/dfaerch/bubblewrap-on-ubuntu)

As referências ilustram opções de política; **a documentação e os perfis da
distribuição instalada são a autoridade** para configurar o AppArmor local.
