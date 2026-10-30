# Changelog

Todas as mudanças relevantes do StemLab. Formato baseado em
[Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/).

## [1.3.0] — 2026-10-30

Versão de estabilidade, segurança e desempenho. Nenhuma funcionalidade foi
removida.

### Corrigido
- **Tarefas presas em execução**: toda a preparação roda dentro do bloco
  protegido e a finalização é consolidada num único ponto; processos externos
  são sempre encerrados no fim, mesmo em erro. Cancelamento pedido no fim da
  separação não marca mais o trabalho como concluído.
- **Arquivo `.zip` temporário** ganhou nome único (`mkstemp`) e é apagado
  depois de enviado, evitando colisão entre downloads simultâneos.
- **Separação sem limite de tempo**: agora há um teto (`MAX_JOB_S`, 6 h) e um
  vigia que encerra o processo no cancelamento **mesmo quando a saída é
  silenciosa**.

### Segurança
- **Token de sessão**: rotas que alteram dados (POST/DELETE) exigem o cabeçalho
  `X-StemLab-Token`, obtido em `GET /api/session` apenas pela própria página.
- **Anti-DNS-rebinding**: o servidor recusa `Host`/`Origin` que não sejam
  `127.0.0.1`/`localhost` na porta 8765; não há CORS.
- **Sem caminhos absolutos na API**: a representação pública do trabalho não
  inclui o caminho do arquivo; `reveal` e `/api/system` deixam de expor pastas
  e o caminho do ffmpeg (agora um booleano).
- **Validação de conteúdo no upload**: o arquivo precisa conter áudio de fato
  (verificado com ffprobe); texto disfarçado, arquivos vazios, corrompidos ou
  vídeos sem faixa de áudio são recusados. Guardas de disco/limite de duração
  respondem 413/507/429.

### Desempenho
- **Forma de onda gerada no servidor** (poucos pontos, decodificação por
  streaming) e cacheada em disco; o navegador não baixa nem decodifica mais o
  áudio inteiro.
- Player usa `preload="metadata"` em vez de carregar todas as faixas.

### Alterado
- **Dependências fixadas** em `requirements.txt` (torch 2.15.0, torchaudio
  2.11.1, demucs 4.1.0, fastapi 0.162.0, uvicorn 0.54.0, python-multipart
  0.0.32, soundfile 0.14.0, numpy 2.5.4).
- **`instalar.bat`** instala a partir do `requirements.txt`, passa a ter um
  passo de **verificação** que aborta se faltar dependência, e o rótulo das
  etapas vai de 1 a 4.
- Comandos do Demucs nunca usam `shell=True` e passam um `--` antes do arquivo
  de origem (nomes com espaços/acentos e até começando com `-` são tratados
  literalmente).
- **Limpeza e retenção**: trabalhos concluídos com mais de 30 dias são
  removidos automaticamente; sobras em `uploads/` e `output/` (sem trabalho
  correspondente) são limpas no arranque e pelo botão **“Limpar temporários”**.

### Adicionado
- `GET /api/waveform/{job}/{nome}?points=` — picos da forma de onda.
- `POST /api/maintenance/cleanup` — limpeza sob demanda.
- Botões **“Limpar concluídos”** e **“Limpar temporários”** no histórico.
- Limites centralizados em `app/config.py`: duração máxima (`MAX_DURATION_S`),
  disco livre mínimo (`MIN_FREE_MB`), uploads simultâneos
  (`MAX_CONCURRENT_UPLOADS`), tempo máximo de trabalho (`MAX_JOB_S`), retenção
  (`RETENTION_DAYS`) e idade mínima de órfãos (`ORPHAN_MIN_AGE_H`).

## [1.0.0] — anterior

- Versão inicial: separação local com Demucs, 7 modelos, player multipista com
  mute/solo, download individual ou em `.zip`, fila e histórico persistente,
  instalador sem exigir Python e empacotamento portátil (`.exe`).
