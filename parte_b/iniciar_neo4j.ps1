# Liga o Neo4j local (portátil, em tools\). Deixe esta janela aberta enquanto usa o banco.
# Neo4j Browser: http://localhost:7474   (usuário neo4j, senha no arquivo .env)
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:JAVA_HOME = Join-Path $root "tools\jdk"
& (Join-Path $root "tools\neo4j\bin\neo4j.bat") console
