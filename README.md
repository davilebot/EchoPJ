# Plataforma Receita — Matcher de CNPJ

Aplicação independente para encontrar e validar CNPJs na base aberta da Receita Federal. O serviço compartilha a base oficial já existente no VPS, mas tem código, interface, autenticação, cache e implantação próprios. O Radar de Concessionárias não é alterado.

## Primeira versão

- consulta individual por nome, endereço, município, UF e CEP;
- envio de CSV com até 200 empresas, preservando a ordem original;
- resultado classificado como confirmado, revisão necessária ou não encontrado;
- download do CSV original acrescido do CNPJ e dos dados do match;
- busca opcional e automática de CNPJ no HTML público do website;
- API `POST /api/matches/batch`, com até 200 entradas;
- somente empresas ativas por padrão.

## Regras de segurança do match

- CEP ou endereço isolado nunca confirma um CNPJ;
- coincidência de nome precisa ser sustentada por localização ou CNPJ direto;
- CNPJ extraído do site passa pelo dígito verificador e pela Receita;
- casos duvidosos ficam para revisão manual;
- são devolvidos score, confiança, sinais, três candidatos e versão da Receita.

## Desempenho medido no VPS

Com a versão `2026-08` e 72.617.105 estabelecimentos:

- 1 empresa: 26–32 ms no benchmark e 18 ms no teste funcional;
- 10 empresas: 4,2–4,5 s;
- 200 empresas: 52–59 s.

Em duas execuções consecutivas das primeiras 200 linhas, o resultado ficou estável em 44 confirmados, 93 para revisão e 63 sem resultado.

A consulta ao website é opcional, tem timeout, respeita `robots.txt` e usa cache de 30 dias.

## Execução

O contêiner exige `POSTGRES_DSN`, `APP_USERNAME` e `APP_PASSWORD` no ambiente. O arquivo `compose.vps.yml` conecta o serviço à rede privada da base compartilhada e a uma rede separada com saída para consultar websites. Nenhum segredo deve ser salvo no repositório.
