# Guia do desenvolvedor

## Código e arquitetura

O projeto usa Python 3.11 ou superior, FastAPI, PostgreSQL e uma interface em
HTML, CSS e JavaScript sem etapa de build. O Dockerfile usa Python 3.12.

| Onde | O que editar |
| --- | --- |
| `service/static/index.html`, `app.js`, `styles.css` | Tela principal, filtros, consultas e exportações |
| `service/static/login.*`, `account.*`, `auth.css` | Login e conta |
| `service/static/organizations.*`, `invite.*`, `workspace.js` | Organizações, equipe e convites |
| `service/static/assets/` | Imagens da interface |
| `service/main.py` | Rotas HTTP, autenticação e integração dos serviços |
| `service/repository.py`, `search.py`, `explorer.py` | Consultas PostgreSQL, filtros e explorador |
| `service/auth.py`, `organizations.py`, `mail.py` | Contas, permissões e envio de convites |
| `service/jobs.py` | Fila, histórico e arquivos de resultados |
| `src/plataforma_receita/` | Normalização, matcher e importação complementar da Receita |
| `tests/` | Testes unitários e testes da API com banco simulado |
| `sql/`, `remote/`, `compose.vps.yml` | Scripts e configuração da infraestrutura existente |

## Instalação e testes

Na raiz do repositório, usando Python 3.11 ou superior:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
PYTHONPATH=src python -m unittest discover -s tests
```

Os testes usam arquivos temporários e simulam o repositório da Receita. Não
precisam de acesso ao VPS, a contas reais ou ao PostgreSQL de produção.
No Windows, ative o ambiente por `.venv\Scripts\Activate.ps1` e defina
`$env:PYTHONPATH = "src"` antes de executar os comandos Python.

## Executar a aplicação

1. Copie `.env.example` para `.env` e crie a pasta `data/`.
2. Preencha uma conexão PostgreSQL de desenvolvimento em `POSTGRES_DSN`.
3. Defina usuário e senha locais em `APP_USERNAME` e `APP_PASSWORD`.
4. Configure HTTPS local e ajuste `APP_PUBLIC_URL` para a mesma origem usada
   no navegador.

```bash
cp .env.example .env
mkdir -p data
```

Com um certificado TLS local confiável em `certs/localhost.pem` e sua chave
em `certs/localhost-key.pem`, execute:

```bash
PYTHONPATH=src python -m uvicorn service.main:app \
  --host 127.0.0.1 --port 8000 --reload \
  --ssl-certfile certs/localhost.pem \
  --ssl-keyfile certs/localhost-key.pem
```

Abra `https://localhost:8000`. Também é possível usar um proxy local que termine
HTTPS e encaminhe para o Uvicorn. O cookie de sessão exige HTTPS (`Secure`);
mantenha essa proteção ao configurar o ambiente. Certificados, chaves, `.env`
e arquivos de dados são ignorados pelo Git.

As credenciais de bootstrap são importadas no primeiro início. Depois disso,
a conta fica em `data/auth.sqlite`, e a senha é alterada pela área Minha conta.
Não copie os arquivos de contas e jobs de produção para o ambiente local.

## Dependência da base da Receita

Clonar este repositório fornece o código, mas não os dados da Receita. A aplicação
espera um PostgreSQL previamente preparado, incluindo `dataset_versions` e
`rfb_establishments`, com estrutura compatível com `service/repository.py`.
As consultas avançadas também usam tabelas complementares quando disponíveis.

O schema inicial e o carregador da base principal pertencem à infraestrutura
compartilhada já existente no VPS. Os scripts em `sql/` são complementares;
sozinhos não montam a base principal em um PostgreSQL vazio. Combine com o
responsável pelo projeto uma amostra de desenvolvimento ou acesso específico
de somente leitura. Para trabalhar na lógica e na API sem essa base, use os testes.

O roteiro da carga complementar está em `docs/carga-completa-receita.md`.

## Docker e produção

Para construir a imagem localmente:

```bash
docker build -t plataforma-receita:dev .
```

`compose.vps.yml` descreve o ambiente EasyPanel existente e depende de redes,
imagem, volumes e segredos daquele servidor. Não é um Compose de desenvolvimento
autossuficiente. Os scripts em `remote/` incluem alterações de infraestrutura e
devem ser revisados antes de execução.

O envio do código ao GitHub não atualiza a aplicação publicada. Mudanças no VPS
precisam de uma implantação separada. Senhas, SMTP, dados de usuários, exportações
e backups ficam fora do repositório.
