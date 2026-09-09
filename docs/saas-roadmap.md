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
- [x] todas as chamadas da interface vinculadas à organização selecionada;
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

- [x] página pública dinâmica de planos, comparação de créditos e explicação transparente do consumo;
- [x] infraestrutura de checkout hospedado Asaas com preços configuráveis, desativada até receber catálogo e credenciais;
- assinatura mensal e pacotes avulsos de créditos;
- [x] endpoint de webhooks Asaas autenticado, idempotente e auditável, com conferência do valor antes de liberar créditos;
- [x] renovação, inadimplência, cancelamento autenticado e reativação, com histórico separado por ciclo e revisão de estornos;
- notas e recibos fornecidos pelo provedor;
- [x] painel interno para consultar organizações, planos e concessões manuais;
- [x] pedidos e eventos de cobrança no painel interno, prontos para receber a integração ativada;
- migração dos dados operacionais para PostgreSQL próprio antes de escalar.

## Entrega 3 — aquisição e ativação

- [x] recuperação segura de senha por link temporário e uso único;
- [x] cadastro verificado por e-mail, protegido por ativação operacional;
- [x] onboarding contextual para salvar a primeira busca, criar lista e convidar a equipe;
- [x] exemplos de segmentos e modelos editáveis de listas;
- [x] medição do funil de cadastro, ativação, entrada em plano comercial e uso recorrente;
- [x] notificações internas, individuais e sem duplicação para saldo baixo e conclusão de processamento;
- avisos de renovação por e-mail após a integração do provedor de cobrança;
- [x] ajuda contextual pesquisável, atalhos por tarefa e diagnóstico seguro do workspace para suporte.

## Entrega 4 — produto comercial maduro

- [x] perfis de administrador, membro e consulta, com bloqueio de escrita, lotes, exportação e créditos no frontend e na API;
- [x] limites seguros de requisições pesadas, tamanho de payload e processamentos ativos por organização;
- [x] centro de privacidade da conta com exportação autenticada, solicitação cancelável de exclusão e fila de tratamento interno;
- [x] infraestrutura para Termos e Privacidade versionados, aceite rastreável e bloqueio seguro do cadastro enquanto os documentos estiverem incompletos;
- aprovação jurídica e preenchimento final da identidade da operadora, retenção e procedimento de exclusão;
- [x] backups SQLite consistentes, verificados e isolados para a v2, com retenção e procedimento de recuperação;
- [x] observabilidade interna com prontidão por componente, latência, erros, identificador de requisição e idade do backup;
- cópia criptografada fora do VPS e alertas externos para o canal operacional escolhido;
- [x] metas iniciais de disponibilidade, latência, erro e recuperação documentadas;
- [x] processo de atendimento dentro do produto, com chamado por organização, histórico, prioridade, fila interna e notificação de resposta;
- [x] teste controlado de prontidão, dashboard e busca contra a base real, com limites reproduzíveis e relatório versionado;
- teste de carga específico para processamento em lote e checkout depois da homologação do provedor;
- [x] página institucional responsiva do produto, com proposta de valor, fluxos, créditos, equipe, segurança e CTAs adaptados à disponibilidade do cadastro;
- domínio comercial e publicação das versões jurídicas aprovadas para clientes.

## Decisões pendentes

Antes da entrega de cobrança, definir:

1. confirmar o Asaas como provedor (integração preparada) e decidir se boleto entra no lançamento ou depois de Pix/cartão;
2. preços, quantidade de créditos e regras de validade de cada plano;
3. quais dados e exportações exigem desbloqueio;
4. política de teste gratuito e reembolso;
5. domínio definitivo e identidade fiscal da empresa vendedora.
