# StemLab

Separador de faixas (stems) **local**, inspirado no fluxo do Moises, mas rodando
inteiramente no seu PC. Você joga um áudio, escolhe o modelo e recebe as faixas
separadas (voz, bateria, baixo, etc.) prontas para ouvir num player multipista
com **mute/solo**, ou baixar.

Motor de IA: [Demucs](https://github.com/facebookresearch/demucs) (Meta, código aberto).

---

## Como usar

1. Na **primeira vez**, dê dois cliques em **`instalar.bat`**. Ele baixa o
   Python 3.13 e as dependências sozinho — **não é preciso ter Python instalado**.
2. Depois, dê dois cliques em **`Iniciar StemLab.bat`**
3. O navegador abre sozinho em `http://127.0.0.1:8765`
4. Arraste um arquivo de música → escolha o modelo → **Separar faixas**
5. Quando terminar, clique em **Ouvir** para abrir o player

Para encerrar: feche a janela preta (ou Ctrl+C nela).

---

## O que tem

| Recurso | Descrição |
|---|---|
| Upload | Arraste e solte: mp3, wav, flac, m4a, ogg, opus, wma, aiff, **mpeg/mpg** e vídeos mp4/webm (só o áudio é usado) — até 800 MB |
| 7 modelos | De "mais rápido" a "alta qualidade" (tabela abaixo) |
| 4 ou 6 faixas | Inclui modelo que separa **guitarra** e **piano** |
| Modo karaokê | Só voz + playback (`--two-stems`) |
| Formatos de saída | WAV 16/24 bits, FLAC, MP3 (até 320 kbps) |
| Player multipista | Faixas sincronizadas, **M** (mute) e **S** (solo), volume por faixa, seek, repetir |
| Waveform | Forma de onda gerada no servidor (poucos pontos, o navegador não baixa o áudio inteiro) |
| Download | Faixa individual ou tudo em `.zip` |
| Fila e histórico | Um trabalho por vez, cancelável, histórico persistente |
| Segurança | Só a interface local altera dados: cada sessão tem um token e o servidor recusa origem/host estranhos (anti-DNS-rebinding) |
| Limpeza | “Limpar concluídos” remove o histórico; “Limpar temporários” apaga sobras e trabalhos com mais de 30 dias |
| Atalhos | `Espaço` = play/pause · `#job=<id>` na URL abre direto um trabalho |

---

## Modelos disponíveis

| Modelo | Faixas | Observação |
|---|---|---|
| `htdemucs` | 4 | **Padrão.** Melhor equilíbrio qualidade/tempo |
| `htdemucs_ft` | 4 | Alta qualidade; ~4x mais lento |
| `htdemucs_6s` | 6 | Separar também guitarra e piano (qualidade menor nesses dois) |
| `hdemucs_mmi` | 4 | Geração anterior do modelo híbrido |
| `mdx_extra` | 4 | Família MDX, boa qualidade |
| `mdx_extra_q` | 4 | Versão quantizada, mais rápida |
| `mdx_q` | 4 | O mais rápido do pacote |

---

## Onde ficam os arquivos

```
stemlab/
├─ Iniciar StemLab.bat   ← use este
├─ instalar.bat          ← instala o ambiente (baixa o Python)
├─ run.py                ← servidor
├─ app/                  ← backend (FastAPI + Demucs)
├─ web/                  ← interface (HTML/CSS/JS)
├─ build_exe.ps1         ← gera o executável portátil (opcional)
├─ stemlab.spec          ← receita do PyInstaller (usada pelo build)
├─ CHANGELOG.md          ← histórico de versões
├─ uploads/              ← áudios enviados
├─ output/<id>/stems/    ← faixas geradas
└─ jobs.json             ← histórico
```

---

## Ambiente técnico (instalado nesta máquina)

- **Python 3.13.17** isolado em `.venv` (o Python 3.15 do sistema **não** serve:
  o PyTorch ainda não publica wheel para ele)
- `torch 2.15.0+cpu` · `torchaudio 2.11.1+cpu` · `demucs 4.1.0` · `fastapi` · `uvicorn`
- `ffmpeg 9.0.2` (via winget) — necessário para ler mp3/m4a

### Desempenho

Roda **100% em CPU**. A placa (Radeon RX 580) não tem CUDA, que é o único
acelerador que o PyTorch usa no Windows — então não há aceleração por GPU.

Referência medida: um áudio de **12 s** levou ~15–18 s com `htdemucs` e `shifts=0`
(já com o modelo em cache). Música de 3–4 min com as configurações padrão
(`shifts=1`) costuma levar de alguns minutos a ~15 min, dependendo do modelo.
O `.zip` e o player aparecem assim que o trabalho termina.

> Estimativas mostradas na interface são orientativas, não garantias.

### Primeira execução

Na primeira separação o Demucs baixa os pesos do modelo (~80 MB) do Hugging Face.
A interface mostra "Carregando modelo (na primeira vez ele é baixado…)" — deixe rodar.

---

## Gerar o executável portátil (opcional)

Se quiser enviar o StemLab para alguém que **não quer instalar nada**, gere o
pacote portátil:

```powershell
.\build_exe.ps1
```

Sai daí:

| Arquivo | O que é |
|---|---|
| `dist\StemLab\StemLab.exe` | o programa — abra este arquivo |
| `StemLab-portatil.zip` | o pacote pronto para enviar (~200 MB) |

A pessoa extrai o `.zip` e abre `StemLab.exe`: o **ffmpeg vai embutido**, então
não precisa instalar ffmpeg nem Python.

Os dados (áudios enviados, faixas e histórico) ficam numa pasta
`StemLab dados`, criada ao lado do `.exe` — assim o pacote não é poluído.

Para gerar, é preciso o `.venv` do `instalar.bat` e o PyInstaller nele:

```powershell
uv pip install --python .venv\Scripts\python.exe pyinstaller imageio-ffmpeg
```

---

## Solução de problemas

| Sintoma | Causa provável / solução |
|---|---|
| "ffmpeg ausente" no cabeçalho | Rode `winget install Gyan.FFmpeg` e reabra o `Iniciar StemLab.bat` |
| Erro ao instalar o ambiente | Rode `instalar.bat`; ele baixa o Python 3.13 e reinstala as dependências |
| Mensagem **"Python não foi encontrado"** (ou abre a Microsoft Store) ao instalar | Use o `instalar.bat` atualizado: ele **não** depende de Python já instalado |
| Separação muito lenta | Use `mdx_q` ou `mdx_extra_q`, e `shifts = 0` |
| "Formato não suportado" | Converta para wav/mp3 antes |
| Porta 8765 ocupada | Feche outra instância do StemLab |

---

## Nota

A análise de engenharia reversa que embasou as escolhas de produto está em
[`ANALISE-REA.md`](ANALISE-REA.md). O StemLab foi construído do zero com
tecnologia aberta — ele **não** é um clone do motor proprietário do Moises.