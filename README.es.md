<div align="center">

<img src="assets/logo.png" alt="AI Collab Skill" width="480">

<br>

# AI Collab Skill

[![License](https://img.shields.io/badge/Licencia-MIT-green?style=for-the-badge)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/gsepcore/ai-collab-skills?style=for-the-badge&logo=github)](https://github.com/gsepcore/ai-collab-skills)
[![Works With](https://img.shields.io/badge/Funciona_Con-Claude_Code-blueviolet?style=for-the-badge)](https://claude.ai/code)
[![Built By](https://img.shields.io/badge/Creado_por-gsepcore-blue?style=for-the-badge)](https://gsepcore.com)

**Permite que múltiples IAs trabajen en el mismo proyecto simultáneamente — y que realmente se vean entre sí.**

Creado por **Luis Alfredo Velasquez Duran** | Alemania, 2025-2026

[GitHub](https://github.com/gsepcore/ai-collab-skills) · [Instalar en 2 comandos](#instalación) · [gsepcore.com](https://gsepcore.com)

🇬🇧 **English?** Read the [English README](README.md)

</div>

---

Cuando usas Claude Code junto con Cursor, Windsurf, Codex, OpenCode u otra herramienta de IA, están completamente ciegas entre sí. Este skill crea un protocolo de filesystem compartido para que puedan leer y escribir contexto en tiempo real — sin servicio externo, sin API, sin internet. Solo una carpeta `.ai-collab/` dentro de tu proyecto.

---

## Cómo funciona

Cada IA escribe un log de sesión en Markdown dentro de `{raíz-del-proyecto}/.ai-collab/`. Cualquier IA con acceso al filesystem del proyecto puede leer esos logs al instante. Claude gestiona su propio log mediante este skill. Las demás IAs participan a través de snippets simples que se agregan a sus archivos de reglas (`.cursorrules`, `.windsurfrules`, etc.) — configuración de una sola vez por proyecto.

```
tu-proyecto/
└── .ai-collab/
    ├── PROTOCOL.md                   ← protocolo compartido (se crea automáticamente)
    ├── claude-20260511-143022.md     ← log de Claude Code
    ├── cursor-20260511-141500.md     ← log de Cursor
    ├── codex-20260511-141000.md      ← log de Codex
    └── opencode-20260511-140500.md   ← log de OpenCode
```

---

## Instalación

### Paso 1 — Instalar los archivos del skill

```bash
mkdir -p ~/.claude/skills/collab/references

curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/SKILL.md \
  -o ~/.claude/skills/collab/SKILL.md

curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/references/protocol.md \
  -o ~/.claude/skills/collab/references/protocol.md
```

O clona y copia manualmente:

```bash
git clone https://github.com/gsepcore/ai-collab-skills.git
mkdir -p ~/.claude/skills/collab/references
cp ai-collab-skills/SKILL.md ~/.claude/skills/collab/SKILL.md
cp ai-collab-skills/references/protocol.md ~/.claude/skills/collab/references/protocol.md
```

### Paso 2 — Configurar tu proyecto

Abre Claude Code dentro de tu proyecto y ejecuta:

```
/collab setup
```

Esto hará:
- Crear la carpeta `{raíz}/.ai-collab/`
- Agregar `.ai-collab/` a `.gitignore` automáticamente
- Copiar el `PROTOCOL.md` al directorio compartido
- Preguntar qué otras IAs usas y generar los snippets de reglas para cada una

### Paso 3 — Activar las otras IAs

Pega este comando en cada otra IA al inicio de su sesión. Reemplaza las rutas con la ruta real de tu proyecto:

```
Eres parte de un equipo de múltiples IAs trabajando en este proyecto simultáneamente.

PASO 1 — Lee estos archivos ahora:
- {raíz-del-proyecto}/.ai-collab/PROTOCOL.md
- {raíz-del-proyecto}/.ai-collab/CONTEXT.md
- Cualquier otro archivo .md en {raíz-del-proyecto}/.ai-collab/ (son logs de otras IAs)

PASO 2 — Confirma que los leíste con un resumen de 3 líneas:
- En qué están trabajando las otras IAs
- Qué archivos NO debes tocar
- El problema crítico más importante pendiente

PASO 3 — Escribe tu primer log en:
{raíz-del-proyecto}/.ai-collab/{nombre-de-tu-ia}-{YYYYMMDD-HHMMSS}.md

Usa este formato exacto:
---
ai: [Nombre de tu IA y modelo]
session: [YYYYMMDD-HHMMSS]
project: [nombre del proyecto]
updated: [timestamp ISO]
---
## Working On
[qué estás haciendo ahora mismo]
## Files Modified This Session
[archivos que tocaste, o "None"]
## Decisions Made
[decisiones tomadas, o "None"]
## Do Not Touch (Avoid Conflicts)
[archivos que estás editando activamente]
## Handoff Note
[lo más importante que las otras IAs deben saber de esta sesión]

REGLA PERMANENTE — después de CADA respuesta que me des:
Actualiza ese archivo con lo que acabas de hacer. No esperes que te lo pida. Siempre.

REGLA DE COORDINACIÓN:
- Antes de editar cualquier archivo, revisa la sección "Do Not Touch" de los otros logs
- Si otra IA tiene un archivo listado, pregúntame antes de tocarlo
- Escribe solo en español o inglés — sin mezclar otros idiomas o alfabetos
```

O para **configuración permanente** (para que la IA lo haga automáticamente en cada sesión), pega los snippets listos de `examples/` en sus archivos de reglas. Ver la tabla de [Herramientas de IA compatibles](#herramientas-de-ia-compatibles) más abajo.

---

## Comandos

### `/collab read`

Lee todos los logs de sesión de las otras IAs que trabajan en este proyecto.

Muestra: nombre de la IA, hora de última actualización, estado activo/idle/stale y el contenido completo del log. Resalta los archivos marcados como "Do Not Touch" para que sepas qué evitar.

```
/collab read
```

### `/collab write [nota opcional]`

Guarda el contexto actual de la conversación de Claude en el directorio compartido.

Crea o actualiza `.ai-collab/claude-{YYYYMMDD-HHMMSS}.md` con lo que estás trabajando, archivos modificados, decisiones tomadas, bugs encontrados y cualquier cosa que las otras IAs deban saber.

```
/collab write
/collab write "terminé el refactor de auth, empezando tests"
```

### `/collab status`

Vista rápida de cada IA activa en el proyecto — nombre, última actualización e indicador de estado.

- 🟢 Activa — actualizada hace menos de 1 hora
- 🟡 Idle — actualizada hace 1–4 horas
- 🔴 Stale — actualizada hace más de 4 horas

```
/collab status
```

### `/collab setup`

Configuración inicial para un proyecto. Ejecutar una sola vez por proyecto.

- Crea la carpeta `.ai-collab/`
- La agrega a `.gitignore`
- Copia `PROTOCOL.md` al directorio
- Pregunta qué herramientas de IA usas y genera los snippets
- Escribe el primer log de Claude

```
/collab setup
```

### `/collab monitor`

Inicia un monitor en segundo plano de costo cero que te notifica en el instante en que otra IA actualiza su log. Corre como script bash persistente — no consume tokens mientras espera.

```
/collab monitor
```

Para detenerlo: dile a Claude *"detén el monitor de collab"* o ejecuta `TaskStop <id>` con el ID que muestra `/collab status`.

---

### `/collab summary`

Genera `.ai-collab/CONTEXT.md` — una síntesis limpia de todos los logs de IA en un único archivo de incorporación.

Este es el comando de **context bootstrapping**. Ejecútalo después de cualquier sesión importante. Cualquier IA nueva que se una al proyecto lee este archivo y está completamente al día en segundos — qué se construyó, qué decisiones se tomaron, qué archivos se tocaron, bugs conocidos, locks activos y un párrafo de resumen.

```
/collab summary
```

**El flujo:**
```
Todas las sesiones de IA → /collab summary → CONTEXT.md
Nueva IA se une        → lee CONTEXT.md  → contexto completo al instante
```

---

### `/collab clear`

Elimina logs de sesión viejos.

```
/collab clear          # elimina logs de más de 24 horas
/collab clear --all    # elimina todo excepto PROTOCOL.md (pide confirmación)
```

---

## Monitoreo persistente (sobrevive suspensión, cierre de sesión y reinicios)

Para uso en producción — este enfoque sobrevive suspensión/reactivación del Mac, reinicios de sesión y reboots del equipo.

### Cómo funciona

Tres componentes trabajan juntos:
1. **Daemon launchd** — servicio del sistema macOS que vigila `.ai-collab/` cada 15 segundos. Corre 24/7, se reinicia automáticamente si falla, se reactiva después de suspensión.
2. **Archivo de notificaciones** — `~/.ai-collab-notifications.json` actúa como cola de mensajes. El daemon escribe aquí; Claude lee aquí.
3. **Hook de Claude Code** — el hook `UserPromptSubmit` revisa la cola cada vez que escribes un mensaje. Si hay notificaciones pendientes, las muestra y limpia la cola.

### Configuración

**Paso 1 — Instalar el script del daemon:**
```bash
curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/install/daemon.sh \
  -o ~/.claude/ai-collab-daemon.sh && chmod +x ~/.claude/ai-collab-daemon.sh
```

**Paso 2 — Registrar el servicio launchd:**
```bash
curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/install/com.gsepcore.ai-collab.plist \
  -o ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist
launchctl load ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist
```

**Paso 3 — Agregar los hooks al proyecto:**
Agrega esto a `.claude/settings.local.json` en tu proyecto (mezclando con la configuración existente):
```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd); CTX=\"$ROOT/.ai-collab/CONTEXT.md\"; NOTIF=\"$HOME/.ai-collab-notifications.json\"; if [ -f \"$CTX\" ]; then echo \"[AI-COLLAB SESSION RECOVERY]\"; echo \"Project: $(basename $ROOT)\"; echo \"---\"; cat \"$CTX\"; echo \"---\"; fi; if [ -f \"$NOTIF\" ]; then CONTENT=$(cat \"$NOTIF\"); if [ \"$CONTENT\" != \"[]\" ] && [ -n \"$CONTENT\" ]; then echo \"[PENDING NOTIFICATIONS FROM OTHER AIs]\"; echo \"$CONTENT\"; echo \"[]\" > \"$NOTIF\"; fi; fi'",
            "timeout": 10
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "bash -c 'FILE=\"$HOME/.ai-collab-notifications.json\"; if [ -f \"$FILE\" ]; then CONTENT=$(cat \"$FILE\"); if [ \"$CONTENT\" != \"[]\" ] && [ -n \"$CONTENT\" ]; then echo \"[AI-COLLAB] Notificaciones pendientes de otras IAs:\"; echo \"$CONTENT\"; echo \"[]\" > \"$FILE\"; fi; fi'",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

**Hook `SessionStart`** — se dispara al abrir una nueva sesión. Lee `CONTEXT.md` e inyecta el contexto completo del proyecto antes de que escribas una sola palabra. Sobrevive a batería agotada, reboots y cierres de sesión.

**Hook `Stop`** — se dispara cuando Claude termina de responder. Regenera automáticamente `CONTEXT.md` a partir de todos los logs usando un script Python. Cero tokens, cero acción del usuario.

**Hook `UserPromptSubmit`** — se dispara en cada mensaje. Muestra notificaciones pendientes de otras IAs al instante, sin costo de tokens en reposo.

**Paso 3b — Instalar el script de resumen:**
```bash
curl -fsSL https://raw.githubusercontent.com/gsepcore/ai-collab-skills/main/install/ai-collab-summary.py \
  -o ~/.claude/ai-collab-summary.py
```

Luego agrega el hook `Stop` a tu `.claude/settings.local.json`:
```json
"Stop": [
  {
    "hooks": [
      {
        "type": "command",
        "command": "python3 ~/.claude/ai-collab-summary.py 2>/dev/null || true",
        "timeout": 15,
        "async": true
      }
    ]
  }
],
```

### Gestionar el daemon

```bash
# Verificar estado
launchctl list | grep ai-collab

# Detener
launchctl unload ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist

# Iniciar
launchctl load ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist

# Ver logs
tail -f /tmp/ai-collab-daemon.log
```

### Desinstalar el daemon

```bash
launchctl unload ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist
rm ~/Library/LaunchAgents/com.gsepcore.ai-collab.plist
rm ~/.claude/ai-collab-daemon.sh
rm ~/.ai-collab-notifications.json
rm ~/.ai-collab-last-check
```

---

## Monitoreo en vivo (notificación automática, costo cero en tokens)

Para que Claude te notifique automáticamente en el instante en que otra IA actualiza su log, ejecuta:

```
/collab monitor
```

Esto inicia un **Monitor bash persistente** que vigila `.ai-collab/` cada 20 segundos en segundo plano. No consume tokens mientras espera — Claude solo se activa cuando se detecta un cambio real.

> **¿Por qué no usar `/loop` con un temporizador?**
> Un cron o loop se dispara en intervalos fijos y envía un prompt a Claude cada N minutos sin importar si hubo cambios. Eso consume tokens de entrada en cada tick — incluso para checks vacíos. El Monitor corre como un script bash puro y solo despierta a Claude ante un cambio real en el archivo.

### Detener el monitor

```
/collab status
```

Esto muestra el ID de la tarea del monitor activo. Luego:

```
TaskStop <task-id>
```

O dile a Claude: *"detén el monitor de collab"* y lo detendrá por ti.

Cerrar la sesión de Claude Code también detiene el monitor automáticamente.

---

## Desinstalación

### Eliminar de un proyecto específico

```bash
rm -rf {raíz-del-proyecto}/.ai-collab/
```

Y elimina la línea `.ai-collab/` de `.gitignore` si la agregaste.

Para otras IAs: elimina el bloque `## AI Collab Protocol` de `.cursorrules`, `.windsurfrules`, o `.github/copilot-instructions.md`.

### Eliminar el skill de Claude Code

```bash
rm -rf ~/.claude/skills/collab/
```

El skill dejará de aparecer en las herramientas disponibles de Claude. Las carpetas `.ai-collab/` de tus proyectos son independientes — eliminar el skill no las borra.

---

## Herramientas de IA compatibles

| Herramienta | Archivo de reglas | Ejemplo |
|-------------|------------------|---------|
| **Cursor** | `.cursorrules` | `examples/cursorrules.example` |
| **Windsurf** | `.windsurfrules` | `examples/windsurfrules.example` |
| **Antigravity IDE** | Prompt de sistema / reglas | `examples/antigravity.example` |
| **VS Code (Copilot)** | `.github/copilot-instructions.md` | `examples/vscode-copilot.example` |
| **GitHub Copilot** | `.github/copilot-instructions.md` | `examples/vscode-copilot.example` |
| **OpenCode / Minimax** | Prompt de sistema / reglas | `references/protocol.md` → sección OpenCode |
| **Codex / GPT** | Prompt de sistema | `references/protocol.md` → sección Codex |
| **Hermes** | Prompt de sistema / reglas | `examples/hermes.example` |
| **Cualquier IA / agente** | Pega el snippet genérico | `examples/generic-any-ai.example` |

Todos los snippets incluyen la **regla de log automático** — cada IA guarda su log después de cada respuesta por defecto, sin que el usuario lo pida. Esto es lo que permite la colaboración en tiempo real.

¿Quieres agregar soporte para una nueva herramienta? Ve [CONTRIBUTING.md](CONTRIBUTING.md).

---

## El formato de log

Todas las IAs escriben logs de sesión con esta estructura:

```markdown
---
ai: Claude Code (claude-sonnet-4-6)
session: 20260511-143022
project: mi-proyecto
updated: 2026-05-11 14:30:22
---

## Working On
Corrigiendo el timeout de autenticación en src/auth.ts — los tokens JWT expiran muy pronto en conexiones lentas.

## Files Modified This Session
- `src/auth.ts` — aumentado el tiempo de expiración de 5min a 15min, agregada lógica de refresh

## Decisions Made
- Expiración JWT de 15min — equilibra seguridad con UX para usuarios con conexión lenta

## Issues Identified
- `src/auth.ts:42` — la lógica de refresh no maneja requests concurrentes (race condition)

## Still In Progress
- Tests unitarios para el flujo de refresh

## Do Not Touch (Avoid Conflicts)
- `src/auth.ts` — en proceso de refactoring, coordinar antes de editar

## Handoff Note
El fix del timeout de auth está completo. La race condition en la línea 42 es lo siguiente a resolver — necesita un mutex o debounce. Los tests aún no están escritos.
```

---

## Reglas de coordinación

Todas las IAs que siguen este protocolo deben respetar estas reglas:

1. **"Do Not Touch" es vinculante** — si un archivo aparece en la sección Do Not Touch de otra IA, pregunta al usuario antes de editarlo
2. **Sin sobreescrituras silenciosas** — si no estás de acuerdo con la decisión de otra IA, díselo al usuario; no cambies el código en silencio
3. **Anunciar contexto al inicio de sesión** — siempre dile al usuario qué encontraste en los logs de las otras IAs
4. **Actualizar el log cuando algo cambie** — no esperes al final de la sesión
5. **Idioma** — escribe en inglés o en el idioma del usuario; nunca mezcles sistemas de escritura

---

## Solución de problemas

**`/collab read` no muestra nada**
Ejecuta `/collab setup` primero. La carpeta `.ai-collab/` puede no existir todavía.

**Otra IA no está escribiendo logs**
Asegúrate de que el snippet `## AI Collab Protocol` esté en su archivo de reglas y que haya leído `PROTOCOL.md`. Dile explícitamente: *"Escribe tu log de sesión en `.ai-collab/{nombre-ia}-{timestamp}.md`"*.

**Los logs aparecen como stale inmediatamente**
La herramienta de IA puede estar escribiendo logs con un timestamp antiguo en el frontmatter. Verifica que `updated:` en el log coincida con la hora real de modificación del archivo.

**El monitor se dispara demasiado seguido o no se dispara**
Ajusta el intervalo de check: `/loop 5m revisa .ai-collab/...` para cada 5 minutos, o detén y reinicia con un intervalo diferente.

**`.ai-collab/` fue commiteado a git**
Agrega `.ai-collab/` a tu `.gitignore`. Ejecuta `git rm -r --cached .ai-collab/` para eliminar el seguimiento sin borrar los archivos localmente.

---

## Contribuir

Los PRs son bienvenidos. Ver [CONTRIBUTING.md](CONTRIBUTING.md).

Las contribuciones más valiosas son soporte para nuevas herramientas de IA — agrega un snippet a `references/protocol.md`, un archivo de ejemplo en `examples/`, y actualiza la tabla de arriba.

---

## Licencia

MIT — creado por [Luis Alfredo Velasquez Duran](https://github.com/LuisvelMarketer) / [gsepcore](https://github.com/gsepcore)

---

## Creado por gsepcore

**[gsepcore](https://github.com/gsepcore)** construye infraestructura open source para agentes de IA — herramientas que los hacen más confiables, seguros y colaborativos.

AI Collab Skill es parte del ecosistema gsepcore. Si te ahorró tiempo, revisa nuestros otros proyectos.

---

## GSEP — La Capa de Seguridad y Evolución para Agentes de IA

**[GSEP (Genomic Self-Evolving Prompts)](https://gsepcore.com)** es el framework que potencia la IA detrás de este skill. Envuelve cualquier LLM con 5 capas de seguridad y hace que los agentes mejoren autónomamente con el tiempo.

- **C3 Content Firewall** — bloquea prompt injection antes de que llegue a tu LLM (57 patrones)
- **C4 Behavioral Immune System** — detecta si la respuesta de tu agente fue manipulada
- **C5 Action Firewall** — previene acciones destructivas (rm -rf, DROP TABLE, etc.) antes de que se ejecuten
- **Evolución autónoma** — los agentes mejoran sus propios prompts basándose en feedback, sin reentrenamiento

**[GSEP-MCP](https://github.com/gsepcore/gsep-mcp)** — agrega la seguridad de GSEP a cualquier agente de IA en 2 minutos mediante el Model Context Protocol. Compatible con Claude Desktop, Cursor, Windsurf, n8n y cualquier cliente MCP.

```json
{
  "mcpServers": {
    "gsep": {
      "command": "npx",
      "args": ["-y", "@gsep/mcp@latest"]
    }
  }
}
```

→ [gsepcore.com](https://gsepcore.com) · [GitHub](https://github.com/gsepcore/gsep) · [npm](https://www.npmjs.com/package/@gsep/core)
