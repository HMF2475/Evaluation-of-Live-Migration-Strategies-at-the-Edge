#include <stdio.h>
#include <unistd.h>

int main()
{
	long long i = 0;
	while(1) {
		printf("%lld\n", ++i);
		sleep(1);
	}
	return 0;
}

