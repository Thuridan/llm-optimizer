# llm-optimizer v2 — revisão e especificação corrigida

Análise de `llm-optimizer.sh` v1.0.2, realizada em 2026-09-06, confrontada com
`docs-ai-memory/` e `docs-rtk/`. Este documento preserva a revisão histórica da
v1 e a especificação que orientou a implementação. **O `.sh` agora implementa
a v2.1.0**, com suporte em `optimizer_support.py` e 46 testes isolados aprovados.
O comportamento implementado, uso e limites estão em
[llm-optimizer.md](llm-optimizer.md). A matriz abaixo inclui verificações ao vivo
ainda pendentes e requisitos do adaptador próprio que foi removido. As linhas
e os resultados da v1 citados abaixo são históricos, não referências à posição
atual do código. Os 15 testes do adaptador antigo foram substituídos por testes
das integrações oficiais, preservação de arquivos e orquestração da v2.

## 1. Resultado da revisão

O instalador já preserva JSON inválido, mantém argumentos posicionais de shell,
usa escrita temporária para registrar hooks e limita a reescrita automática.
Essas melhorias devem permanecer. Entretanto, ainda existem problemas de
permissão, integridade da instalação, configuração e validação.

Os 15 testes existentes passaram. Provas adicionais, executadas apenas em
diretórios temporários e com subprocessos simulados, mostraram:

| Experimento | Resultado atual |
|---|---|
| RTK retorna `rtk git status` com status 0, 1, 2, 3 ou 127 | O adaptador Codex emite `permissionDecision: allow` em todos os casos |
| Registrar hook em `hooks.json` que aponta para outro arquivo | O link simbólico é substituído por arquivo comum; o destino original fica intacto |
| Registrar novamente após mudar o caminho do hook | Permanecem duas entradas, uma antiga e uma nova |
| Verificar download sem hash esperado | `conferir_sha256` retorna sucesso |

O experimento de status demonstra descarte da informação de permissão. Não
prova, sozinho, que toda versão do Codex dispense sua aprovação nativa: isso
exige teste do agente real. Os mocks dos testes antigos nem sequer fornecem
`returncode`, portanto não detectam esse defeito.

## 2. Problemas e correções

As referências abaixo apontam para as linhas da versão analisada.

### P0 — preservar decisões de permissão

**Local:** [reescrever e adaptador Codex](llm-optimizer.sh#L632).

`reescrever()` usa apenas stdout. O contrato documentado do RTK distingue
0 = rewrite/Allow, 1 = sem equivalente, 2 = Deny e 3 = rewrite/Ask. O adaptador
descarta essa distinção e sempre emite Allow. Sua lista de comandos de leitura
não substitui uma política de permissão: configurações locais, opções e programas
auxiliares também influenciam o comportamento dos comandos.

**Correção:** representar o resultado como decisão tipada, separada do comando:

| Status RTK | Tratamento v2 |
|---|---|
| 0 | Aceitar candidato válido; aplicar somente se o contrato do agente preservar suas permissões |
| 1 | Não alterar a chamada original |
| 2 | Não executar candidato; traduzir Deny conforme o contrato verificado do agente |
| 3 | Preservar Ask; nunca converter silenciosamente para Allow |
| Outro, timeout ou resposta inválida | Não aplicar candidato; registrar diagnóstico limitado |

Se um agente não consegue combinar Ask/Deny e reescrita, não instalar esse
adaptador automaticamente. Manter a integração oficial por instruções até haver
um contrato testado. A degradação por falha do otimizador não pode descartar uma
negação válida emitida pela política do RTK. Nunca atualizar hashes de confiança
do agente para contornar sua aprovação.

Referências: [RTK técnico](docs-rtk/contributing/TECHNICAL.md),
[agentes suportados](docs-rtk/guide/getting-started/supported-agents.md).

### P1 — configuração e serviço apontam para locais diferentes

**Local:** [configurar_servico_aim](llm-optimizer.sh#L391).

O `init` recebe `--data-dir` e `--config ~/.config/ai-memory/config.toml`, mas
systemd e `nohup` recebem somente `serve`, bind e web. O padrão documentado do
config é dentro do diretório de dados. Variáveis de ambiente podem ampliar essa
divergência. Clientes e instaladores de hooks/MCP também usam opções implícitas.

**Correção:** resolver uma única configuração efetiva antes de qualquer escrita:
binário, versão, modo local/remoto, data dir, config, URL/base path e origem da
autenticação. Passar explicitamente os caminhos ao servidor e usar o mesmo
endpoint nos clientes. Preservar escolhas existentes; não criar uma segunda
instância local quando a intenção é usar um servidor remoto.

Unidades novas devem usar argv corretamente escapado para systemd, incluindo
especificadores `%` e expansão própria do systemd; aspas de shell não bastam.
Preferir o modelo oficial de serviço. Configuração de provedor precisa existir
no serviço persistente, não apenas no shell que executou o instalador.

### P1 — instalação incompleta do ai-memory

**Local:** [instalar_ai_memory](llm-optimizer.sh#L361).

O arquivo de release é extraído, apenas o executável é copiado e o restante é
apagado. Os guias documentam descoberta do bundle `hooks/` junto ao executável.
Uma instalação limpa pode falhar, enquanto uma instalação antiga pode mascarar
o problema reutilizando hooks em cache.

**Correção:** instalar o layout necessário completo em diretório versionado,
validar executável e bundle e só então publicar o caminho estável. Preservar a
versão anterior para recuperação. Não escolher arbitrariamente o primeiro
arquivo chamado `ai-memory` encontrado no tarball.

### P1 — checksum opcional e downloads não reproduzíveis

**Local:** [conferir_sha256](llm-optimizer.sh#L239),
[downloads RTK](llm-optimizer.sh#L510).

Falha ao obter checksum ou ausência de ferramenta retorna sucesso. Release e
checksum são buscados independentemente em `latest`, que pode mudar entre as
requisições. Download não tem limite global de duração.

**Correção:** resolver uma versão uma vez e buscar artefato e checksum dessa
versão. Hash ausente, malformado, ambíguo ou divergente bloqueia a instalação.
Python `hashlib`, já disponível como dependência, pode verificar SHA-256.
Adicionar limites de conexão/transferência e retries limitados. Inspecionar o
layout do arquivo antes da extração; rejeitar caminhos absolutos, travessia e
links que escapem do destino. Validar a instalação temporária antes da promoção.
Checksum publicado no mesmo canal verifica integridade, não comprova sozinho
autenticidade independente.

### P1 — relatório pode destruir um arquivo existente

**Local:** [abertura do relatório](llm-optimizer.sh#L80),
[montagem final](llm-optimizer.sh#L943).

`: > "$RELATORIO"` trunca qualquer destino antes do preflight. Assim,
`--report arquivo-existente` pode destruir conteúdo, inclusive durante dry-run.
O nome padrão tem resolução de segundos. A montagem final usa temporário em
outro diretório, não verifica todas as falhas e pode fazer `mv` entre filesystems.

**Correção:** relatório novo por padrão, criação exclusiva e nome sem colisões.
Rejeitar destino existente e links simbólicos. Gerar em temporário no mesmo
diretório, conferir escrita e publicar atomicamente. Falha de relatório deve
aparecer em stderr e no exit status. Redigir logs sem credenciais ou payloads.

### P1 — integridade de hooks e configurações

**Local:** [escrever_hook](llm-optimizer.sh#L544),
[registrar_hook_json](llm-optimizer.sh#L754).

O hook Python é sobrescrito diretamente, podendo ficar parcial após interrupção.
O JSON usa `os.replace`, mas substitui symlinks. Não há backup próprio nem proteção
contra duas instalações que leem o mesmo estado e sobrescrevem alterações uma da
outra. Deduplicar somente pela string do comando deixa entradas antigas após
mudança de caminho, e uma entrada idêntica com matcher incorreto impede reparo.

**Correção:** validar a estrutura inteira antes da alteração; preservar destino
de symlink e guardar backup; usar lock entre instâncias do instalador e comparar
hash/inode antes da publicação para detectar edições externas. Escrever, validar,
sincronizar e substituir atomicamente o hook. Identificar entradas pertencentes
ao instalador por manifesto e assinatura exata, migrá-las e verificar matcher,
evento e timeout. Preservar todos os hooks estrangeiros. Formato de ownership
não pode inventar campos que o agente não suporta.

### P1 — descoberta não identifica servidor saudável

**Local:** [servidor_aim_responde](llm-optimizer.sh#L357),
[validar_ai_memory](llm-optimizer.sh#L471).

Qualquer aplicação que devolva 200/400/401/405 em `/mcp` é aceita como ai-memory.
401 prova apenas que autenticação é necessária. `status` com falha gera aviso,
sem marcar a validação como falha. Preservar uma unidade existente não verifica
se ela serve o endpoint pretendido. Habilitá-la quando há outro processo na porta
pode causar conflito no próximo login.

**Correção:** separar alcançável, identificado, autenticado, saudável e persistente.
Validar resposta estruturada e configuração efetiva do serviço. Reutilizar servidor
externo identificado sem habilitar automaticamente um concorrente. Não aplicar
configuração dependente de um servidor cuja preparação falhou.

### P1 — caminho do executável muda entre fases

**Local:** [garantir_path](llm-optimizer.sh#L253),
[instalar_rtk](llm-optimizer.sh#L510),
[resolução no hook](llm-optimizer.sh#L585).

Se `~/.local/bin` já estiver no PATH depois de outro diretório, a instalação de
RTK ali não garante que chamadas posteriores por nome usem esse arquivo. O hook
refaz `shutil.which` a cada execução. `rtk gain` falhar por problema de configuração
ou banco não prova colisão com outro projeto. `ai-memory` existente é aceito sem
verificar capacidades, podendo ser wrapper Docker em vez de servidor nativo.

**Correção:** resolver e validar executáveis concretos, classificar native/wrapper,
usar caminhos absolutos consistentes e testar capacidades necessárias. Não
sobrescrever ferramentas existentes só porque um comando de analytics falhou.

### P2 — seleção e dependências têm efeitos prematuros

**Local:** [dependências](llm-optimizer.sh#L223),
[seleção](llm-optimizer.sh#L308).

O índice apt é atualizado mesmo sem dependência ausente. Pacotes e arquivos de
shell são alterados antes da seleção de agentes. Mesmo pulando os dois componentes
essas fases executam. Dependência obrigatória ausente apenas incrementa erros e
permite prosseguir. Agente desconhecido vira aviso; diretório de configuração
antigo é tratado como instalação disponível.

**Correção:** validar argumentos, seleção e requisitos primeiro. Planejar apenas
dependências do trabalho selecionado; atualizar índice uma vez, apenas quando
necessário. Bloquear fases dependentes após erro. Separar agente executável de
configuração residual. Parâmetro inválido termina com status 2 antes de mutações.
Pular ambos deve produzir um no-op explícito ou erro de uso, sem instalação.

### P2 — plataforma e sessão do usuário

**Local:** [arquitetura](llm-optimizer.sh#L132), detecção de systemd e uso de `$USER`.

O script escolhe artefatos Linux somente por CPU. Não valida Bash 4 antes de usar
arrays associativos. Detectar systemd do host não garante user bus disponível.
Alpine recebe só um aviso de incompatibilidade de libc. Com `set -u`, `$USER`
ausente pode interromper a instalação/relatório. Root parametriza seu próprio HOME,
que pode não ser o usuário pretendido.

**Correção:** declarar v2 inicialmente Linux/Bash 4+, validar SO/CPU/libc e sessão
antes de escrever. Usar identificação de usuário robusta e explícita. Validar
`systemctl --user`; oferecer modo de serviço compatível sem mascarar falha como
persistência. Habilitar linger apenas quando previsto no plano de instalação.
Suporte macOS/Windows exige implementação própria, não reutilização destes URLs.

### P2 — escopo, routing e finalização incompletos

**Local:** [parametrizar_ai_memory](llm-optimizer.sh#L457),
[lembretes](llm-optimizer.sh#L501).

Não é passado `--project-strategy repo-root`, apesar da descrição anterior.
Hooks são instalados antes do MCP, sem contexto explícito de endpoint. O script
não instala o pacote de routing/instruções do ai-memory. Só menciona finalização
no Antigravity; Codex também precisa desse cuidado no contrato documentado.

**Correção:** resolver endpoint e escopo, registrar MCP, instalar hooks com a
estratégia planejada e instalar routing pelo mecanismo oficial preservando
instruções existentes. Explicar finalização por agente e versão. Não interpretar
todo `Stop` como fim de sessão. Não finalizar uma sessão durante smoke test real.

### P2 — dry-run e diagnóstico

Dry-run ainda cria/trunca relatório, cria temporários e pode invocar `rtk gain`
para descoberta; esse comando não é uma promessa de ausência de escrita local.
O teste atual verifica somente que um HOME temporário fica vazio. O logger do
hook grava stdin inteiro quando `RTK_HOOK_LOG` está ativo, sem limite nem redação.
`executar` acumula toda a saída em memória e seus chamadores ocultam diagnósticos.

**Correção:** dry-run faz apenas leitura e imprime plano; relatório só quando
explicitamente pedido, sempre novo. Não chamar analytics nem instaladores para
descobrir capacidades. Logs de hook contêm somente motivo/status/tamanho, com
limite e permissões privadas. Limitar stdin e captura de stdout/stderr. Usar
logs temporários privados e cauda limitada para processos demorados.

### P2 — validação superficial e resultados incorretos

`grep ai-memory` não verifica eventos, URLs ou configuração MCP. Encontrar
`updatedInput` não prova preservação de argv/permissões. Um hook que falha sem
stdout pode passar o teste negativo de `git commit`. A saída do relatório chama
agentes selecionados de parametrizados, mesmo após falhas. Funções que terminam
em `erro()` podem retornar 0 porque o logger retorna sucesso; o chamador ignora
`configurar_servico_aim`.

**Correção:** retornos explícitos, cadeia de dependências e estado por componente
e agente: planejado, aplicado, validado, bloqueado ou falhou. Verificar status do
processo e JSON completo. Diferenciar configuração escrita de integração
comprovada em sessão real. Só concluir sucesso quando todas as fases requeridas
estiverem concluídas.

## 3. Arquitetura otimizada da v2

```text
argumentos e plataforma
        ↓
descoberta somente leitura
        ↓
plano: componentes, versões, caminhos, endpoint, agentes, persistência
        ↓
preflight e validação de conflitos
        ↓
download verificado → staging completo → validação → publicação
        ↓
serviço local OU reutilização de servidor existente
        ↓
MCP → hooks de captura → routing/instruções
        ↓
RTK oficial → adaptadores opcionais com contrato comprovado
        ↓
validação por camada → relatório estruturado
```

Separar o orquestrador Bash do adaptador Python e de fixtures de teste. Evitar
reimplementar instaladores oficiais quando já preservam formatos, symlinks e
hooks estrangeiros. A v2 deve manter um manifesto local privado com versões,
arquivos próprios, hashes anteriores e backups; nenhum segredo no manifesto.

Uma segunda execução com o mesmo plano não deve reescrever arquivos idênticos,
duplicar hooks ou reiniciar serviços saudáveis. Atualizações devem ser explícitas
e identificar a versão-alvo. Falha em ai-memory bloqueia sua parametrização,
mas pode permitir RTK independente; o resultado geral continua indicando erro.

Os nomes de futuras opções de CLI são decisões de implementação. Nenhum comando
novo deste documento deve ser entendido como já disponível em v1.0.2.

## 4. Contrato correto das ferramentas

| Aspecto | Comportamento que a v2 deve descrever |
|---|---|
| RTK | Compactação de saída; não é sandbox ou classificador seguro de comandos |
| Integração RTK | Codex/Antigravity por instruções oficiais neste snapshot; hooks próprios são extensão local |
| Comandos compostos | RTK documenta lexer com suporte parcial; recusa ampla é política conservadora nossa |
| Recuperação de saída | `rtk proxy` para saída bruta; tee tem limites de tamanho/retenção |
| Economia | Estimativa por bytes/tokens; não garantia de redução de fatura |
| ai-memory sem LLM | Captura, pesquisa e síntese determinística de sessões substantivas funcionam |
| ai-memory 2.0 | Embeddings locais best-effort por padrão quando nenhum provedor está configurado |
| Auto-improve com LLM | Scheduler e aprovação separados; propostas auto-aprovadas por padrão na documentação |
| Privacidade | Observações sanitizadas e limitadas; não promessa de transcript completo ou DLP universal |
| Backup | Wiki e estado operacional do banco; reindex não recupera sessões/credenciais |
| Escopo | Basename padrão de instalação; repo-root precisa ser escolhido explicitamente |
| Telemetria RTK | Preservar consentimento e escolha existente; não habilitar por exemplo de config |

Adicionar LLM, embeddings remotos ou managed workstreams não é necessário para
instalar captura básica. Essas escolhas têm efeitos de recurso, custo e dados e
precisam constar do plano, sem inferência a partir de credenciais encontradas.

## 5. Matriz de aceitação

| Área | Verificação necessária |
|---|---|
| Permissões | Fixtures de status 0/1/2/3/erro; Ask nunca vira Allow; Deny não executa original como fallback |
| Agentes | Payloads reais sanitizados e versão identificada; preservar cwd, timeout, argv e campos adicionais |
| Downloads | Checksum ausente/incorreto, mudança de latest, timeout, layout inesperado e libc incompatível |
| Instalação | HOME limpo sem cache de hooks; bundle completo; executável publicado corresponde ao validado |
| Arquivos | Symlinks, JSON inválido, espaços/aspas, disco cheio, interrupção, concorrência e alteração externa |
| Idempotência | Mesma execução sem diff; mudança de caminho migra somente entrada própria |
| Serviço | Config/data/env consistentes, user bus indisponível, unidade estrangeira, listener de outro produto e 401 |
| Endpoint | Local e remoto com base path; autenticação inválida falha sem expor token |
| Dry-run | Nenhuma escrita implícita, pacote, daemon, analytics, hook ou configuração; relatório novo só se solicitado |
| Relatório | Destino existente preservado; falha de escrita não retorna sucesso; estado aplicado/validado distinto |
| Ciclo de vida | Captura e finalização em servidor e sessão descartáveis; não consumir handoff do projeto real |
| Regressão | Os 15 casos atuais permanecem; mocks de rewrite passam a incluir returncode |

Testes unitários não substituem o teste de permissão no agente real. Até esse
teste existir, o caminho padrão de v2 para Codex deve ser a integração oficial
por instruções, sem afirmar que `permissionDecision: allow` preserva aprovação.

## 6. Ordem de implementação

1. Corrigir decisão RTK, colisão de relatório e checksum obrigatório.
2. Implementar configuração efetiva única, resolução de binários e layout completo.
3. Preservar symlinks, adicionar backups/conflitos e tornar publicação atômica.
4. Reorganizar preflight, seleção, dependências, serviço e bloqueio de fases.
5. Completar MCP, captura, routing, escopo e finalização por agente.
6. Melhorar diagnóstico, limites, dry-run e matriz de validação.
7. Executar aceitação isolada e depois comprovar integrações nas versões-alvo.

Fontes: [análise integral das ferramentas](TOOL-DOCUMENTATION-ANALYSIS.md),
[instalação ai-memory](docs-ai-memory/install.md),
[MCP](docs-ai-memory/mcp-install.md),
[escopo](docs-ai-memory/auto-scope.md),
[marcador](docs-ai-memory/marker-file.md),
[operações](docs-ai-memory/lifecycle-ops.md),
[segurança](docs-ai-memory/security.md),
[testes existentes](tests/test_optimizer.py).
