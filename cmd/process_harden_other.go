//go:build !linux

package cmd

// hardenGatewayProcess is a no-op off Linux: the /proc environ exposure it
// closes is Linux-specific.
func hardenGatewayProcess() {}
