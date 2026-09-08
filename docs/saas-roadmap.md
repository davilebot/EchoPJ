# Roteiro do EchoPJs SaaS

Este documento descreve a evolução da aplicação paralela `saas-v2`. A versão
atual permanece na branch `main` e não recebe estas mudanças.

## Modelo comercial

- A organização é a unidade de assinatura, saldo, equipe, listas e histórico.
- Busca e prévia são gratuitas. Um crédito desbloqueia uma empresa quando ela é
  salva ou exportada pela primeira vez pela organização.
- O mesmo CNPJ pode ser usado em várias listas sem nova cobrança.
- A organização interna EchoHub usa o plano `internal` com créditos ilimitados.
- Novas organizações começam no plano `trial` com o saldo definido por
  `SAAS_TRIAL_CREDITS`.
- Entradas futuras de pagamento usarão uma chave idempotente no livro-razão para
  impedir crédito duplicado quando um webhook for reenviado.

## Entrega 1 — fundação comercial

- [x] implantação separada da aplicação atual;
- [x] acesso e histórico isolados por organização;
- [x] perfil comercial, saldo e livro-razão por organização;
- [x] EchoHub com créditos ilimitados;
- [x] desbloqueio único de CNPJ por organização;
- [x] listas compartilhadas pela equipe;
- [x] buscas salvas com os filtros completos;
- [x] navegação agrupada em “Buscar empresas”;
- [x] telas responsivas de listas, buscas salvas e créditos;
- [x] painel da organização com saldo, listas, buscas e processamentos;
- [x] exportações debitadas e geradas no servidor, com trilha de auditoria;
- [x] catálogo técnico restrito à organização interna.

## Entrega 2 — cobrança e administração

- página pública de planos e comparação de limites;
- checkout hospedado pelo provedor de pagamento;
- assinatura mensal e pacotes avulsos de créditos;
- webhooks assinados, idempotentes e auditáveis;
- renovação, inadimplência, cancelamento e reativação;
- notas e recibos fornecidos pelo provedor;
- painel interno para consultar organizações, planos, pagamentos e concessões;
- migração dos dados operacionais para PostgreSQL próprio antes de escalar.

## Entrega 3 — aquisição e ativação

- [x] recuperação segura de senha por link temporário e uso único;
- cadastro verificado por e-mail;
- onboarding para criar organização, convidar equipe e realizar a primeira busca;
- exemplos de segmentos e modelos de listas;
- medição do funil de cadastro, ativação, compra e retenção;
- notificações de saldo baixo, renovação e conclusão de processamento;
- ajuda contextual e central de suporte.

## Entrega 4 — produto comercial maduro

- permissões granulares além de administrador e membro;
- limites de uso e proteção contra abuso por organização;
- termos de uso, política de privacidade, retenção e exclusão de dados;
- observabilidade, alertas, backups testados e plano de recuperação;
- testes de carga, metas de disponibilidade e atendimento;
- domínio comercial, páginas institucionais e documentação para clientes.

## Decisões pendentes

Antes da entrega de cobrança, definir:

1. provedor de pagamento e necessidade de Pix, boleto e cartão;
2. preços, quantidade de créditos e regras de validade de cada plano;
3. quais dados e exportações exigem desbloqueio;
4. política de teste gratuito e reembolso;
5. domínio definitivo e identidade fiscal da empresa vendedora.
