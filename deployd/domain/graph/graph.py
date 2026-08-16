"""Decision for the graph trasversal algorithm , this decision will determinie what relationshios
we can discover from the evidence we've already collected.
Four separate Problems:
1-Observation: What did we observe?
2-Detection: What relationships or anomalies can we infer from observations?
3-Graph Trasversal: What part of that representation do we explore?
4-Retrival: Which historical incidents resemble what we found?

For example:
{ Deploy->Config Change->DB_Connection->DATA_WRITTEN }
Where DB_CONNECTION is pointing to test DB while the env is production/staging.
Trasversal cannot discover that directly by itself
We need evidence and relations such as : prod->should_use->prod_db && service->actually_uses->test_db
Only after we have that , a deterministic logic can say: expected_dependency!=actual_dependency thus config drift

So to address the trasversal algorithm im thinking Neighborhood expansion

for example: Lets say we have an event: {Process_Crash} find related nodes with k hops: Process_Crash<-Deploy<-Commit;
Process_Crash->Health_Check_Fail->Service. So basically BFS with constraints. this will awnser things like what happend around this error

for causal we have A->Causal->B->Causal->C this will give us a causal chain.
Other basic trasversal Temporal trasversal: Following edge timelines to reconstruct event ordering
t1 deploy
t2 config change
t3 crash
t4 restart

Also important is Dependency trasversal: checkout depending on payment , payment on redis and redis on network.
if checkout fails we do not insepct just checkout but we expand into the dependency neighborhood

"""
