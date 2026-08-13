# Safe Python Compute

K.I.T.T. exposes a ferramenta "python_compute" para cálculos e transformações determinísticas sem fornecer ao código gerado pelo modelo um shell Python geral.

## Fronteira de segurança

O fonte é analisado com ast.parse e interpretado nó por nó por SafePythonInterpreter. Ele nunca é passado a eval, exec, compile, interpretador interativo ou shell.

O interpretador executa em subprocesso novo iniciado com:

- executável Python atual;
- modo isolado -I;
- -S para não carregar site;
- diretório temporário vazio;
- ambiente mínimo sem credenciais herdadas;
- nenhum path do usuário ou projeto em sys.path;
- timeout de relógio;
- RLIMITs Unix para CPU, address space, tamanho de arquivo, descritores e core dump quando suportados.

O subprocesso é defesa em profundidade. A fronteira primária é o interpretador AST, pois um processo executado sob o mesmo usuário do sistema operacional não constitui sozinho um sandbox de filesystem.

## Capacidades disponíveis

- Constantes compatíveis com JSON.
- Listas, tuplas, sets e dicionários.
- Atribuições e atribuições aumentadas.
- Aritmética, comparações, booleanos e slices.
- if e for limitados.
- Comprehensions de lista, set e dict.
- Métodos selecionados de string/list/dict/set.
- Builtins puros selecionados.
- Funções em allowlist de math, statistics e json.
- Decimal e Fraction.
- print capturado.
- Variável explícita de saída, "_result" por padrão.

"inputs" é a única fonte de dados externa controlada pelo modelo e deve ser compatível com JSON.

## Capacidades explicitamente indisponíveis

- import e imports dinâmicos;
- filesystem e open;
- sockets e rede;
- subprocessos e shell;
- variáveis de ambiente;
- reflexão, atributos privados e dunder traversal;
- classes e funções;
- generators, coroutines, async, context managers, exceptions e while;
- threads e processos;
- pacotes externos;
- escrita no repositório.

Acesso ao projeto deve ocorrer por read_file, search, repository_map ou ferramenta controlada pela policy. Alterações passam por apply_patch e aprovação.

## Requisição da ferramenta

~~~json
{
  "code": "values = [v * 2 for v in inputs['values']]\n_result = statistics.mean(values)",
  "inputs": {
    "values": [1, 2, 3]
  },
  "result_var": "_result"
}
~~~

O resultado é JSON com stdout capturado, resultado selecionado e contagem de passos.

## Limites

Os defaults ficam em SafePythonConfig:

- bytes de fonte e requisição;
- quantidade de nós AST;
- passos do interpretador;
- itens em coleções;
- tamanho de resultados e valores;
- saída capturada;
- address space do subprocesso;
- segundos de CPU;
- wall-clock.

Os limites são aplicados dentro do interpretador e, quando suportados, pelo sistema operacional.

## Por que pacotes externos não são expostos

Módulos de terceiros ampliariam a superfície de capacidades e herdariam CVEs, código nativo, dependências transitivas e comportamentos inesperados de I/O. Até bibliotecas numéricas podem carregar plugins, arquivos, shared libraries ou configurações do ambiente.

Se uma capacidade futura exigir biblioteca externa, exponha uma operação estreita controlada pelo host em vez de tornar o pacote importável pelo modelo. Exija testes de contrato, pinning, origem, revisão de vulnerabilidades e feature flag explícita.

## Limitações conhecidas

Este é um ambiente de cálculo semelhante a Python, não compatibilidade completa com CPython nem sandbox formalmente verificado. A segurança depende de manter todos os nós AST, callables, atributos, métodos e tipos de entrada em allowlist positiva.

Não adicione getattr, setattr, type, object, vars, locals, globals, compile, eval, exec, __import__, callables arbitrários, atributos arbitrários, pickle, regex com entrada ilimitada ou objetos fornecidos por plugins.

Python irrestrito deve exigir container ou VM administrado separadamente, sem credenciais e rede, mounts read-only, volume descartável, quotas e saída apenas por patch. Não faça downgrade silencioso de execução irrestrita para isolamento apenas por processo.
