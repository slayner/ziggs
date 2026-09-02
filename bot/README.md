# Bot Discord

Um bot Discord em Python com arquitetura escalável usando cogs e hybrid commands.

## 📋 Requisitos

- Python 3.8+
- discord.py 2.3.2+
- python-dotenv

## 🚀 Instalação

1. **Clonar/Criar o projeto:**
```bash
cd hideout
```

2. **Criar ambiente virtual:**
```bash
python -m venv venv
```

3. **Ativar ambiente virtual:**

   **Windows:**
   ```bash
   venv\Scripts\activate
   ```

   **Linux/Mac:**
   ```bash
   source venv/bin/activate
   ```

4. **Instalar dependências:**
```bash
pip install -r requirements.txt
```

5. **Configurar token:**
   - Abra `.env` e substitua `seu_token_aqui` pelo seu token do Discord
   - Obtenha seu token em: https://discord.com/developers/applications

## 🤖 Como executar

```bash
python main.py
```

## 📚 Estrutura do Projeto

```
hideout/
├── main.py              # Arquivo principal do bot
├── .env                 # Variáveis de ambiente (ignorado pelo Git)
├── .gitignore          # Arquivos ignorados
├── requirements.txt    # Dependências
├── README.md           # Este arquivo
└── cogs/               # Pasta com os cogs (comandos)
    ├── __init__.py
    └── avatar.py       # Cog exemplo: comando /avatar
```

## 🎮 Comandos

### Avatar
- **Comando:** `/avatar @user` ou `!avatar @user`
- **Descrição:** Mostra o avatar de um usuário
- **Uso:** 
  - `/avatar @João` - Mostra o avatar do João
  - `/avatar` - Mostra seu próprio avatar

## 📝 Como criar novos cogs

1. Crie um arquivo Python em `cogs/`
2. Use a estrutura:

```python
import discord
from discord.ext import commands
from discord import app_commands

class MeuCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.hybrid_command(name='meucomando')
    async def meu_comando(self, ctx):
        await ctx.send('Resposta!')

async def setup(bot):
    await bot.add_cog(MeuCog(bot))
```

3. O cog será carregado automaticamente na inicialização!

## 🔐 Segurança

- **Nunca** comite o arquivo `.env`
- Mantenha seu token seguro
- Use as permissões mínimas necessárias para seu bot

## 📖 Referências

- [Discord.py Documentation](https://discordpy.readthedocs.io/)
- [Discord Developer Portal](https://discord.com/developers/applications)

---

**Feito com ❤️**
