from neural_node_swarm.cli import main


def test_cli_supports_sqlite_backend(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["neural-node-swarm", "start", "--storage", "sqlite", "--memory", str(tmp_path / "memory.db")])
    main()
    assert "events: 3" in capsys.readouterr().out
