# Plataforma Receita

Projeto independente para consulta e enriquecimento de dados empresariais brasileiros.

O piloto inicial usa as primeiras 200 linhas do CSV do Apollo, preserva a ordem original e consulta a base compartilhada da Receita sem alterar o Radar de Concessionárias.

## Regras do piloto

- somente empresas ativas são selecionadas automaticamente;
- CEP ou endereço isolado nunca confirma um CNPJ;
- casos duvidosos ficam em revisão;
- o resultado registra candidatos, pontuação, sinais e versão da Receita;
- o quadro societário é uma relação separada, com uma linha por sócio ou administrador.

