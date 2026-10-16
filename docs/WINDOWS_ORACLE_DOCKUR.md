# Windows Oracle em um Dockur já instalado

O KITT **não** precisa de outra VM para usar `kitt-re-win11` como referência Win32.
A VM do Dockur, porém, é só o ambiente de referência: `docker ps` não comprova
login Windows, canal de probe, licenças ou prontidão do Oracle. Esta integração
**não recria o contêiner, não altera disco e não publica novas portas**.

## Contratos já implementados

- `kitt re status` permanece 100% local e não ativa automaticamente o Oracle.
- `kitt re oracle doctor --manifest-sha256 <sha256>` verifica o contêiner
  gerenciado, o perfil SSH privado do operador e faz uma solicitação
  `{"version":1,"method":"probe.info","probe":null}` ao guest.
- `kitt re status --check-oracle --manifest-sha256 <sha256>` incorpora o
  diagnóstico ao JSON dessa invocação. Fora desse modo, `oracle_ready`
  continua falso por segurança. Nenhuma capacidade é persistida a partir do
  diagnóstico; verificações futuras precisam conferir a identidade novamente.
- `probe.info` retorna platform, release, architecture, hash SHA-256
  **dos bytes reais do manifesto no Windows** e contagem de probes.
  Não executa programas.
- `kitt vm lab doctor --hosts <arquivo>` valida o formato do perfil host
  anterior; `kitt vm lab run` continua exigindo **dois** guests com
  `probe.run` autenticado, reference=Windows e candidate=Linux+Wine.
  A existência do toolbox `kitt-re-full` **não** o transforma em VM de testes.

## Preparação manual e deliberada dentro do Windows

1. Abra o console local em http://127.0.0.1:8006 e confirme o Windows
   realmente inicializado, arquitetura x64, versão/build e licença apropriada.
2. Em uma conta com autorização administrativa **dentro da VM**, instale
   OpenSSH Server Windows e uma versão compatível de Python (>=3.14) e
   `kitt-agent-cli` (versão compatível) usando mídia/pacotes aprovados.
   A instalação não é feita pelo KITT host nem força downloads.
3. Crie um usuário Windows **sem privilégios administrativos**, exclusivo
   para o serviço de probes, e uma chave SSH específica. No OpenSSH do
   Windows configure a autenticação só por chave e um **ForceCommand**
   apontando para o módulo instalado `guest_rpc`, com argumentos fixos:
   
   ```text
   python -m kitt.extensions.builtin_plugins.reverse_engineering.guest_rpc --probe-root C:\kitt-probes --manifest C:\kitt-probes\manifest.json
   ```

   O caminho de `python` deve ser absoluto e verificado dentro do guest.
   Configure `PermitTTY no`, desabilite port-forwarding, agent-forwarding e
   shell interativo para essa conta. Use as regras efetivas do OpenSSH/Windows
   da versão instalada; não copie um trecho de `sshd_config` sem validá-lo.
   Uma conexão SSH com comando arbitrário precisa continuar restrita ao
   endpoint **forçado**, sem disponibilizar PowerShell/cmd.exe.
4. Prepare somente probes próprios, benignos, em `C:\kitt-probes`.
   Exemplo de manifesto **dentro do Windows** (substitua SHA e arquivos
   pelos valores realmente calculados):
   
   ```json
   {
     "probes": {
       "event_auto_reset": {
         "path": "event_probe.exe",
         "sha256": "sha256-minusculo-de-64-caracteres-gerado-no-guest"
       }
     }
   }
   ```
   
   Use `Get-FileHash -Algorithm SHA256 C:\kitt-probes\event_probe.exe`
   para preencher o SHA real de 64 caracteres (sem prefixo).
   O probe autoral de sincronização está em
   `examples/reverse_engineering/windows_event_probe.c`; compile com um
   compilador C e SDK oficialmente licenciados, registre hash e valide
   a execução local primeiro. Não rode binários desconhecidos nessa VM
   nem execute provas de isolamento que o host não verificou.
5. Após gravar o manifesto, obtenha seu hash:
   
   ```powershell
   (Get-FileHash -Algorithm SHA256 C:\kitt-probes\manifest.json).Hash.ToLowerInvariant()
   ```
   
   Guarde o hash de manifesto como pin aprovado pelo operador fora do
   workspace editável pela IA.

## Conectividade privada a partir do Linux

A instalação atual do laboratório publica **somente**
`127.0.0.1:8006 -> 8006/tcp`. **Não há mapeamento SSH publicado no host.**
O NAT interno do Dockur não é um servidor SSH acessível automaticamente.

O operador precisa estabelecer uma rota de gerenciamento **previamente
autorizada** para o guest, sem recriar disco/contêiner nem expor SSH na
LAN, e conferir HostName/Port reais e fingerprint SSH do guest. Uma rota
pode ser obtida por rede privada apropriada ou túnel autenticado, mas não
se deve assumir que o IP `172.30.0.2` é alcançável no host. Não desabilite
a verificação de host key (`StrictHostKeyChecking`) e não monte o Docker
socket, chaves pessoais ou o filesystem sensível do host.

Crie arquivo SSH privado com alias `kitt-win-oracle` e valores **verificados**:

```sshconfig
Host kitt-win-oracle
    HostName <endereco-privado-alcancavel-e-verificado>
    Port <porta-de-gerenciamento-verificada>
    User <usuario-de-probes-sem-admin>
    IdentityFile ~/.ssh/kitt-oracle-ed25519
    UserKnownHostsFile ~/.ssh/kitt-oracle-known_hosts
    StrictHostKeyChecking yes
    BatchMode yes
    ForwardAgent no
    RequestTTY no
```

Os caracteres entre `< >` são placeholders, **não** parâmetros funcionais.
O fingerprint deve ser conferido fora de banda pelo operador; executar
`ssh-keyscan` sem conferir a identidade não estabelece confiança.

Configure `~/.kitt/reverse-engineering/hosts.json`, modo `0600` e dono
do operador, usando o schema já aceito pelo `mission.py`:

```json
{
  "schema_version": 1,
  "ssh_config": "/home/OPERADOR/.ssh/kitt-lab-config",
  "reference": "kitt-win-oracle",
  "candidate": "kitt-wine-guest"
}
```

O segundo alias deve apontar para outra VM autorizada com Wine e a
mesma versão de `guest_rpc`. O `candidate` pode estar temporariamente
indisponível para o **doctor do Oracle**, mas é obrigatório para missões
diferenciais. A escolha do endereço, chaves e rotas é do operador, não
da IA. Não grave segredos na pasta do projeto.

## Validar o Windows Oracle

No host Linux, depois de confirmar a rota, o host key e o manifest SHA:

```bash
kitt re oracle doctor --manifest-sha256 SHA256_REAL_DO_MANIFESTO
kitt re status --check-oracle --manifest-sha256 SHA256_REAL_DO_MANIFESTO
```

Ambos aceitam `--hosts /CAMINHO/PRIVADO/hosts.json` para perfil não padrão.
O `doctor` devolve `oracle_ready: true` **somente** quando todos os
checks passaram nesta execução. Resposta incorreta, Windows não x64,
perfil ausente, erro SSH, manifesto divergente ou contêiner alterado
retorna `BLOCKED_ENV` e não executa um probe.

O resultado `guest_attested: false` é proposital: host key e hash de
manifesto não fornecem atestação criptográfica da VM.

## Primeiro teste diferencial

Depois do Oracle **e** guest Wine preparados, crie um manifesto de missão
no workspace com `{"schema_version":1,"probes":["event_auto_reset"]}`.
A execução ocorre apenas pela política do host, com os comandos do CLI
documentados em `docs/REVERSE_ENGINEERING_AUTONOMY.md`.

Nenhum resultado real de Windows/Wine, porcentagem de paridade ou E2E
foi obtido nesta implementação. Até existir uma rota autenticada
verificada dentro da VM existente, a integração permanece
`BLOCKED_ENV`, mesmo que o contêiner reporte `running`.
