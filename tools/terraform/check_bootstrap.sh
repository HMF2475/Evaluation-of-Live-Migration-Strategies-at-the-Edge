while true; do
	all_done=true

	for n in edge-node-1 edge-node-2 edge-host-1; do
		echo "=== $n ==="

		if ! multipass exec "$n" -- bash -c "true" >/dev/null 2>&1; then
			echo "UNREACHABLE: multipass exec timeout/failed"
			all_done=false
			continue
		fi

		if multipass exec "$n" -- bash -c "sudo grep -q 'Node fully provisioned.' /var/log/node-bootstrap.log 2>/dev/null"; then
			echo "bootstrap: DONE"
		else
			echo "bootstrap: IN PROGRESS"
			all_done=false
		fi

		multipass exec "$n" -- bash -c '
			criu --version 2>/dev/null | head -1 || true
			sudo podman --version
		'
	done

	if $all_done; then
		echo "Bootstrap finished on all nodes."
		break
	fi

	sleep 5
done
