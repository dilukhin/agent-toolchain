# Политика обновления managed tools

**Статус:** product contract

## 1. Production source и installed revision — разные понятия

Для first-party managed tool ветка, которую upstream-проект считает production-ready (`main`, `master` либо явно указанная иная ветка), может быть authoritative source желаемого состояния.

`agent-toolchain` не вводит дополнительный период «вылёживания» и не поддерживает второй approval pin только ради задержки после merge upstream.

При этом installed runtime никогда не публикуется как mutable `@main`/`@master` объект. В начале одного reconciliation-run production branch разрешается в exact 40-hex commit SHA:

```text
production branch
        ↓ resolve once
exact commit SHA
        ↓
versioned immutable runtime
        +
bound skills from the same exact SHA
        ↓
health / entrypoint / manifest
```

Если branch изменится после resolution, это относится уже к следующему `check`/`apply`.

## 2. `follow-branch`

ToolSpec policy:

```json
{
  "source": "git",
  "repo": "https://github.com/OWNER/REPO.git",
  "branch": "main",
  "update_policy": "follow-branch"
}
```

В persistent config при `follow-branch` fixed `ref` не задаётся.

Reconciler:

1. read-only получает HEAD production branch;
2. валидирует exact 40-hex SHA;
3. использует этот SHA как immutable execution identity для всего текущего reconciliation-run;
4. runtime и tool-owned skills получают один и тот же resolved SHA;
5. versioned release path строится по exact SHA;
6. полный SHA записывается в ownership manifest/markers.

Недоступный или неоднозначный branch resolution не является разрешением использовать произвольный ref: автоматическая mutation останавливается.

## 3. Когда сохраняется fixed pin

`pinned-tested` остаётся допустимой явной policy для сторонней/экспериментальной dependency или upstream, где выбранная branch не является production-ready контрактом.

Это отдельный режим, а не обязательный второй promotion gate для first-party tools.

## 4. Текущие first-party policies

- `ssh_relay` — `follow-branch`, production branch `main`;
- `agent-safe` — `follow-branch`, production branch `master` (текущий default/production branch upstream);
- `proxy-tools` — `bundled-with-setup`.

`tunnelctl` и `bundle` включаются в managed registry только после реализации соответствующей runtime family; при добавлении source policy выбирается по фактическому upstream distribution contract.

## 5. Diagnostic identity

Human/log diagnostic identity для git-sourced utility рекомендуется представлять как:

```text
<tool> MAJOR.MINOR.PATCH.BUILD
```

где `BUILD` — первые 8 hex символов exact source commit SHA.

Пример:

```text
ssh_relay 0.9.1.39dea792
```

Это не заменяет provenance: ownership manifest и machine-readable metadata хранят полный 40-hex SHA.

При инциденте human identity позволяет быстро сопоставить log line с exact source revision, а полный SHA остаётся authoritative идентификатором.

## 6. Идемпотентность

`follow-branch` не отменяет идемпотентность.

Если branch HEAD не изменился:

```text
apply → no-op
```

Если upstream production branch получил новый commit, desired state действительно изменился и следующий reconciliation должен обновить versioned runtime.

## 7. Safety

- `check` остаётся read-only: remote branch lookup не является mutation;
- unknown/unavailable remote state не разрешает destructive fallback;
- новый Python venv создаётся сразу в final versioned release path;
- старый исправный release не модифицируется in-place;
- entrypoint переключается только после health нового exact runtime;
- skill source обязан соответствовать тому же exact SHA, что runtime.
