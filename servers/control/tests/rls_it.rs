mod harness;

use harness::{admin_dsn, fresh_database, ROLE_LOCK};
use ufo_control::rls::{
    bootstrap_policies, control_password, ensure_control_role, ensure_serve_role, serve_password,
    CONTROL_ROLE, IDLE_IN_TRANSACTION_TIMEOUT, OWNER_ROLE, POLICY_NAME, SERVE_ROLE,
};

const TENANT_TABLES: &str = "create table workspace (id uuid primary key); \
     create table member (id uuid primary key, workspace_id uuid not null, email text not null); \
     create table alembic_version (version_num text primary key)";

#[tokio::test]
async fn bootstrap_policies_scopes_every_public_table_and_skips_alembic() {
    let database = fresh_database().await;
    let mut client = database.client().await;
    client.batch_execute(TENANT_TABLES).await.unwrap();
    bootstrap_policies(&mut client).await.unwrap();

    for (table, column) in [("workspace", "id"), ("member", "workspace_id")] {
        let row = client
            .query_one(
                "select rel.relrowsecurity, pol.qual, pol.with_check, pol.cmd, pol.permissive \
                 from pg_class rel join pg_namespace ns on ns.oid = rel.relnamespace \
                 left join pg_policies pol on pol.schemaname = ns.nspname \
                   and pol.tablename = rel.relname and pol.policyname = $2 \
                 where ns.nspname = 'public' and rel.relname = $1",
                &[&table, &POLICY_NAME],
            )
            .await
            .unwrap();
        assert!(row.get::<_, bool>("relrowsecurity"), "{table} has RLS off");
        let expected = format!("({column} = (current_setting('app.workspace_id'::text))::uuid)");
        assert_eq!(row.get::<_, Option<String>>("qual"), Some(expected.clone()));
        assert_eq!(row.get::<_, Option<String>>("with_check"), Some(expected));
        assert_eq!(row.get::<_, Option<String>>("cmd").as_deref(), Some("ALL"));
        assert_eq!(
            row.get::<_, Option<String>>("permissive").as_deref(),
            Some("PERMISSIVE")
        );
    }

    let alembic: bool = client
        .query_one(
            "select relrowsecurity from pg_class where relname = 'alembic_version'",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    assert!(!alembic, "alembic_version must not be policed");
}

#[tokio::test]
async fn bootstrap_is_idempotent_and_recreates_a_drifted_policy() {
    let database = fresh_database().await;
    let mut client = database.client().await;
    client.batch_execute(TENANT_TABLES).await.unwrap();
    bootstrap_policies(&mut client).await.unwrap();
    bootstrap_policies(&mut client).await.unwrap();

    client
        .batch_execute(&format!(
            "drop policy {POLICY_NAME} on member; \
             create policy {POLICY_NAME} on member using (true)"
        ))
        .await
        .unwrap();
    bootstrap_policies(&mut client).await.unwrap();
    let qual: Option<String> = client
        .query_one(
            "select qual from pg_policies where tablename = 'member' and policyname = $1",
            &[&POLICY_NAME],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(
        qual.as_deref(),
        Some("(workspace_id = (current_setting('app.workspace_id'::text))::uuid)"),
        "a drifted policy is replaced rather than kept"
    );
}

#[tokio::test]
async fn bootstrap_fails_loud_on_an_unpoliced_table() {
    let database = fresh_database().await;
    let mut client = database.client().await;
    client
        .batch_execute("create table stray (id uuid primary key)")
        .await
        .unwrap();
    let refused = bootstrap_policies(&mut client)
        .await
        .unwrap_err()
        .to_string();
    assert!(refused.contains("stray"), "{refused}");
    assert!(refused.contains("workspace_id"), "{refused}");
}

#[tokio::test]
async fn the_serve_role_is_created_with_its_grants_and_dbos_database() {
    let _roles = ROLE_LOCK.lock().await;
    let database = fresh_database().await;
    let client = database.client().await;
    client.batch_execute(TENANT_TABLES).await.unwrap();
    std::env::set_var("UFO_CONTROL_PG_ROLE_SEED", "ufo-test-seed");
    database.ensure_owner_role(&client).await;

    ensure_serve_role(&client).await.unwrap();
    ensure_serve_role(&client).await.unwrap();

    let exists: Option<i32> = client
        .query_opt("select 1 from pg_roles where rolname = $1", &[&SERVE_ROLE])
        .await
        .unwrap()
        .map(|row| row.get(0));
    assert!(exists.is_some());
    assert_eq!(serve_password().unwrap().len(), 64);

    for table in ["workspace", "member"] {
        let granted: bool = client
            .query_one(
                "select has_table_privilege($1, $2, 'select')",
                &[&SERVE_ROLE, &table],
            )
            .await
            .unwrap()
            .get(0);
        assert!(granted, "{SERVE_ROLE} was not granted {table}");
    }

    let dbos = format!("{}_dbos", database.name);
    let present: Option<i32> = client
        .query_opt("select 1 from pg_database where datname = $1", &[&dbos])
        .await
        .unwrap()
        .map(|row| row.get(0));
    assert!(present.is_some(), "{dbos} was not created");
}

#[tokio::test]
async fn both_shared_roles_carry_the_idle_in_transaction_timeout() {
    let _roles = ROLE_LOCK.lock().await;
    let database = fresh_database().await;
    let client = database.client().await;
    std::env::set_var("UFO_CONTROL_PG_ROLE_SEED", "ufo-test-seed");
    database.ensure_owner_role(&client).await;
    ensure_serve_role(&client).await.unwrap();

    for role in [SERVE_ROLE, OWNER_ROLE] {
        let settings: Option<Vec<String>> = client
            .query_one(
                "select rolconfig from pg_roles where rolname = $1",
                &[&role],
            )
            .await
            .unwrap()
            .get(0);
        let settings = settings.unwrap_or_default();
        assert!(
            settings.iter().any(|setting| setting
                == &format!("idle_in_transaction_session_timeout={IDLE_IN_TRANSACTION_TIMEOUT}")),
            "{role} carries {settings:?}"
        );
    }
}

#[tokio::test]
async fn the_control_role_reaches_its_own_schema_and_no_tenant_table() {
    let _roles = ROLE_LOCK.lock().await;
    let database = fresh_database().await;
    let client = database.client().await;
    client.batch_execute(TENANT_TABLES).await.unwrap();
    std::env::set_var("UFO_CONTROL_PG_ROLE_SEED", "ufo-test-seed");
    database.ensure_owner_role(&client).await;
    ensure_serve_role(&client).await.unwrap();
    ensure_control_role(&client).await.unwrap();
    ensure_control_role(&client).await.unwrap();
    bootstrap_policies(&mut database.client().await)
        .await
        .unwrap();

    assert_eq!(control_password().unwrap().len(), 64);
    assert_ne!(control_password().unwrap(), serve_password().unwrap());

    for table in ["workspace", "member"] {
        for privilege in ["select", "insert", "update", "delete"] {
            let granted: bool = client
                .query_one(
                    "select has_table_privilege($1, $2, $3)",
                    &[&CONTROL_ROLE, &table, &privilege],
                )
                .await
                .unwrap()
                .get(0);
            assert!(
                !granted,
                "{CONTROL_ROLE} holds {privilege} on {table} — the fence is open"
            );
        }
    }

    let usage: bool = client
        .query_one(
            "select has_schema_privilege($1, 'ufo_control', 'usage')",
            &[&CONTROL_ROLE],
        )
        .await
        .unwrap()
        .get(0);
    assert!(usage, "{CONTROL_ROLE} cannot use its own schema");
}

#[tokio::test]
async fn a_select_on_a_tenant_table_as_the_control_role_is_denied() {
    let _roles = ROLE_LOCK.lock().await;
    let database = fresh_database().await;
    let mut client = database.client().await;
    client.batch_execute(TENANT_TABLES).await.unwrap();
    std::env::set_var("UFO_CONTROL_PG_ROLE_SEED", "ufo-test-seed");
    database.ensure_owner_role(&client).await;
    ensure_serve_role(&client).await.unwrap();
    ensure_control_role(&client).await.unwrap();
    bootstrap_policies(&mut client).await.unwrap();

    for table in ["workspace", "member"] {
        let granted: bool = client
            .query_one(
                "select has_table_privilege($1, $2, 'select')",
                &[&CONTROL_ROLE, &table],
            )
            .await
            .unwrap()
            .get(0);
        assert!(!granted, "{CONTROL_ROLE} holds select on {table}");
    }

    client
        .batch_execute(&format!("set role \"{CONTROL_ROLE}\""))
        .await
        .unwrap();
    let refused = client
        .query("select count(*) from member", &[])
        .await
        .unwrap_err();
    let code = refused.code();
    assert!(
        code == Some(&tokio_postgres::error::SqlState::INSUFFICIENT_PRIVILEGE)
            || code == Some(&tokio_postgres::error::SqlState::UNDEFINED_OBJECT),
        "the control role read a tenant table: {:?} / {:?}",
        code,
        refused.as_db_error().map(|error| error.message())
    );
}

#[tokio::test]
async fn a_new_table_receives_no_serve_grant_before_the_policy_bootstrap() {
    let _roles = ROLE_LOCK.lock().await;
    let database = fresh_database().await;
    let client = database.client().await;
    std::env::set_var("UFO_CONTROL_PG_ROLE_SEED", "ufo-test-seed");
    database.ensure_owner_role(&client).await;
    client.batch_execute(TENANT_TABLES).await.unwrap();
    ensure_serve_role(&client).await.unwrap();

    client
        .batch_execute("create table later (id uuid primary key, workspace_id uuid not null)")
        .await
        .unwrap();
    let granted: bool = client
        .query_one(
            "select has_table_privilege($1, 'later', 'select')",
            &[&SERVE_ROLE],
        )
        .await
        .unwrap()
        .get(0);
    assert!(
        !granted,
        "the default privileges revoke did not hold — `later` is readable before its policy"
    );
}

#[tokio::test]
async fn the_admin_dsn_names_a_database_this_suite_can_reach() {
    assert!(admin_dsn().starts_with("postgres"), "{}", admin_dsn());
}
