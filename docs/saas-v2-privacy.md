# Privacidade operacional da v2

## Controles disponíveis

A página **Minha conta** permite ao titular:

- baixar, após confirmar a senha, um JSON com cadastro, sessões sem tokens,
  vínculos com organizações, aceites jurídicos, ações administrativas e solicitações de exclusão;
- consultar a versão dos Termos de Uso e da Política de Privacidade aceita no cadastro;
- registrar uma solicitação de exclusão com motivo opcional;
- acompanhar o estágio informado pela equipe interna;
- cancelar a solicitação enquanto ela estiver ativa.

O arquivo nunca inclui hash de senha, salt, token de sessão ou token de convite.
A exportação cobre a conta pessoal. Listas, buscas, créditos e processamentos
pertencem ao workspace e devem seguir o procedimento definido para a empresa.

## Documentos e aceite no cadastro

As páginas públicas `/termos` e `/privacidade` leem a identidade da empresa
operadora, os contatos, a vigência e a política de retenção da configuração do
servidor. Cada documento possui uma versão independente.

O cadastro público só fica disponível quando SMTP, ativação operacional e todos
os campos `LEGAL_*` estão configurados. O formulário apresenta as versões atuais,
exige aceite expresso e envia as duas versões à API. Se qualquer versão mudar entre
a leitura e o envio, a API recusa o cadastro e pede que a página seja recarregada.
Após a confirmação do e-mail, os aceites são gravados com usuário, organização,
versão, data e origem.

## Tratamento interno

Administradores da organização interna EchoHub veem a fila em **Administração**.
Os estágios são: recebida, em análise, aguardando usuário, aprovada, cancelada e
encerrada. Aprovar uma solicitação não executa remoção automática: a equipe deve
primeiro confirmar titularidade, vínculos com workspaces, obrigações de retenção
e eventual transferência da administração.

## Pendências para publicação jurídica

A infraestrutura e as páginas já estão prontas, mas o texto exibido ainda é uma
minuta técnica. Antes de abrir o cadastro comercial, é necessário obter aprovação
jurídica, preencher a razão social ou nome empresarial, CNPJ, endereço e contatos,
definir os prazos de retenção e aprovar o procedimento definitivo de exclusão.
Somente então as versões podem ser configuradas como publicadas.
