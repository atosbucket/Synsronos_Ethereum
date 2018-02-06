import unittest

from utils.deployutils import compile_contracts, attempt_deploy, mine_tx, MASTER, DUMMY, take_snapshot,\
    restore_snapshot, fresh_account, fresh_accounts, UNIT, fast_forward
from utils.testutils import assertReverts, assertClose, block_time
from utils.generalutils import to_seconds

ESCROW_SOURCE = "contracts/HavvenEscrow.sol"
HAVVEN_SOURCE = "contracts/Havven.sol"
NOMIN_SOURCE = "contracts/EtherNomin.sol"


def setUpModule():
    print("Testing HavvenEscrow...")


def tearDownModule():
    print()


class TestHavvenEscrow(unittest.TestCase):
    def setUp(self):
        self.snapshot = take_snapshot()

    def tearDown(self):
        restore_snapshot(self.snapshot)

    @classmethod
    def setUpClass(cls):
        cls.assertReverts = assertReverts
        cls.assertClose = assertClose

        compiled = compile_contracts([ESCROW_SOURCE, HAVVEN_SOURCE, NOMIN_SOURCE])
        cls.havven, txr = attempt_deploy(compiled, 'Havven', MASTER, [MASTER])
        cls.nomin, txr = attempt_deploy(compiled, 'EtherNomin', MASTER, [cls.havven.address, MASTER, MASTER, 1000 * 10**18, MASTER])
        cls.escrow, txr = attempt_deploy(compiled, 'HavvenEscrow', MASTER,
                                         [MASTER, cls.havven.address, cls.nomin.address])
        mine_tx(cls.havven.functions.setNomin(cls.nomin.address).transact({'from': MASTER}))

        cls.h_totalSupply = lambda self: cls.havven.functions.totalSupply().call()
        cls.h_targetFeePeriodDurationSeconds = lambda self: cls.havven.functions.targetFeePeriodDurationSeconds().call()
        cls.h_feePeriodStartTime = lambda self: cls.havven.functions.feePeriodStartTime().call()
        cls.h_endow = lambda self, sender, receiver, amt: mine_tx(cls.havven.functions.endow(receiver, amt).transact({'from': sender}))
        cls.h_balanceOf = lambda self, account: cls.havven.functions.balanceOf(account).call()
        cls.h_transfer = lambda self, sender, receiver, amt: mine_tx(cls.havven.functions.transfer(receiver, amt).transact({'from': sender}))
        cls.h_recomputeLastAverageBalance = lambda self, sender: mine_tx(cls.havven.functions.recomputeLastAverageBalance().transact({'from': sender}))

        cls.n_updatePrice = lambda self, sender, price: mine_tx(cls.nomin.functions.updatePrice(price).transact({'from': sender}))
        cls.n_setTransferFeeRate = lambda self, sender, rate: mine_tx(cls.nomin.functions.setTransferFeeRate(rate).transact({'from': sender}))
        cls.n_issue = lambda self, sender, quantity, value: mine_tx(cls.nomin.functions.issue(quantity).transact({'from': sender, 'value': value}))
        cls.n_burn = lambda self, sender, quantity: mine_tx(cls.nomin.functions.burn(quantity).transact({'from': sender}))
        cls.n_buy = lambda self, sender, quantity, value: mine_tx(cls.nomin.functions.buy(quantity).transact({'from': sender, 'value': value}))
        cls.n_sell = lambda self, sender, quantity: mine_tx(cls.nomin.functions.sell(quantity).transact({'from': sender}))
        cls.n_purchaseCostEther = lambda self, quantity: cls.nomin.functions.purchaseCostEther(quantity).call()
        cls.n_balanceOf = lambda self, account: cls.nomin.functions.balanceOf(account).call()
        cls.n_transfer = lambda self, sender, recipient, quantity: mine_tx(cls.nomin.functions.transfer(recipient, quantity).transact({'from': sender}))
        cls.n_feePool = lambda self: cls.nomin.functions.feePool().call()
        cls.n_nominPool = lambda self: cls.nomin.functions.nominPool().call()

        cls.owner = lambda self: cls.escrow.functions.owner().call()
        cls.setOwner = lambda self, sender, newOwner: mine_tx(cls.escrow.functions.setOwner(newOwner).transact({'from': sender}))

        cls.e_havven = lambda self: cls.escrow.functions.havven().call()
        cls.e_nomin = lambda self: cls.escrow.functions.nomin().call()
        cls.vestingSchedules = lambda self, account, index, i: cls.escrow.functions.vestingSchedules(account, index, i).call()
        cls.numVestingEntries = lambda self, account: cls.escrow.functions.numVestingEntries(account).call()
        cls.getVestingScheduleEntry = lambda self, account, index: cls.escrow.functions.getVestingScheduleEntry(account, index).call()
        cls.getVestingTime = lambda self, account, index: cls.escrow.functions.getVestingTime(account, index).call()
        cls.getVestingQuantity = lambda self, account, index: cls.escrow.functions.getVestingQuantity(account, index).call()
        cls.totalVestedAccountBalance = lambda self, account: cls.escrow.functions.totalVestedAccountBalance(account).call()
        cls.totalVestedBalance = lambda self: cls.escrow.functions.totalVestedBalance().call()
        cls.getNextVestingIndex = lambda self, account: cls.escrow.functions.getNextVestingIndex(account).call()
        cls.getNextVestingEntry = lambda self, account: cls.escrow.functions.getNextVestingEntry(account).call()
        cls.getNextVestingTime = lambda self, account: cls.escrow.functions.getNextVestingTime(account).call()
        cls.getNextVestingQuantity = lambda self, account: cls.escrow.functions.getNextVestingQuantity(account).call()
        
        cls.feePool = lambda self: cls.escrow.functions.feePool()
        cls.setHavven = lambda self, sender, account: mine_tx(cls.escrow.functions.setHavven(account).transact({'from': sender}))
        cls.setNomin = lambda self, sender, account: mine_tx(cls.escrow.functions.setNomin(account).transact({'from': sender}))
        cls.sweepFees = lambda self, sender: mine_tx(cls.escrow.functions.sweepFees().transact({'from': sender}))
        cls.withdrawContractFees = lambda self, sender: mine_tx(cls.escrow.functions.withdrawContractFees().transact({'from': sender}))
        cls.purgeAccount = lambda self, sender, account: mine_tx(cls.escrow.functions.purgeAccount(account).transact({'from': sender}))
        cls.withdrawHavvens = lambda self, sender, quantity: mine_tx(cls.escrow.functions.withdrawHavvens(quantity).transact({'from': sender}))
        cls.appendVestingEntry = lambda self, sender, account, time, quantity: mine_tx(cls.escrow.functions.appendVestingEntry(account, time, quantity).transact({'from': sender}))
        cls.addVestingSchedule = lambda self, sender, account, time, quantity, periods: mine_tx(cls.escrow.functions.addVestingSchedule(account, time, quantity, periods).transact({'from': sender}))
        cls.vest = lambda self, sender: mine_tx(cls.escrow.functions.vest().transact({'from': sender}))

    def make_nomin_velocity(self):
        # should produce a 36 * UNIT fee pool
        self.n_updatePrice(MASTER, UNIT)
        self.n_setTransferFeeRate(MASTER, UNIT // 100)
        self.n_issue(MASTER, 1000 * UNIT, 2000 * UNIT)
        self.n_buy(MASTER, 1000 * UNIT, self.n_purchaseCostEther(1000 * UNIT))
        for i in range(8):
            self.n_transfer(MASTER, MASTER, (9 - (i + 1)) * 100 * UNIT)
        self.n_sell(MASTER, self.n_balanceOf(MASTER))
        self.n_burn(MASTER, self.n_nominPool())

    def test_constructor(self):
        self.assertEqual(self.e_havven(), self.havven.address)
        self.assertEqual(self.e_nomin(), self.nomin.address)
        self.assertEqual(self.owner(), MASTER)
        self.assertEqual(self.totalVestedBalance(), 0)

    def test_vestingTimes(self):
        alice = fresh_account()
        time = block_time()
        times = [time + to_seconds(weeks=i) for i in range(1, 6)]
        self.appendVestingEntry(MASTER, alice, times[0], UNIT)
        self.assertEqual(self.getVestingTime(alice, 0), times[0])

        for i in range(1, len(times)):
            self.appendVestingEntry(MASTER, alice, times[i], UNIT)
        for i in range(1, len(times)):
            self.assertEqual(self.getVestingTime(alice, i), times[i])

    def test_vestingQuantities(self):
        alice = fresh_account()
        time = block_time()
        times = [time + to_seconds(weeks=i) for i in range(1, 6)]
        quantities = [UNIT * i for i in range(1, 6)]
        self.appendVestingEntry(MASTER, alice, times[0], quantities[0])
        self.assertEqual(self.getVestingQuantity(alice, 0), quantities[0])

        for i in range(1, len(times)):
            self.appendVestingEntry(MASTER, alice, times[i], quantities[i])
        for i in range(1, len(times)):
            self.assertEqual(self.getVestingQuantity(alice, i), quantities[i])

    def test_vestingSchedules(self):
        alice = fresh_account()
        time = block_time()

        self.appendVestingEntry(MASTER, alice, time + 1000, UNIT)
        self.assertEqual(self.vestingSchedules(alice, 0, 0), time + 1000)
        self.assertEqual(self.vestingSchedules(alice, 0, 1), UNIT)
        self.appendVestingEntry(MASTER, alice, time + 2000, 2 * UNIT)
        self.assertEqual(self.vestingSchedules(alice, 1, 0), time + 2000)
        self.assertEqual(self.vestingSchedules(alice, 1, 1), 2 * UNIT)

    def test_totalVestedAccountBalance(self):
        alice = fresh_account()
        time = block_time()

        self.h_endow(MASTER, self.escrow.address, 100 * UNIT)
        self.assertEqual(self.totalVestedAccountBalance(alice), 0)
        self.appendVestingEntry(MASTER, alice, time + 100, UNIT)
        self.assertEqual(self.totalVestedAccountBalance(alice), UNIT)

        self.purgeAccount(MASTER, alice)
        self.assertEqual(self.totalVestedAccountBalance(alice), 0)

        k = 5
        for n in [100 * 2**i for i in range(k)]:
            self.appendVestingEntry(MASTER, alice, time + n, n)

        self.assertEqual(self.totalVestedAccountBalance(alice), 100 * (2**k - 1))
        fast_forward(110)
        self.vest(alice)
        self.assertEqual(self.totalVestedAccountBalance(alice), 100 * (2**k - 1) - 100)

    def test_totalVestedBalance(self):
        alice, bob = fresh_accounts(2)
        time = block_time()

        self.h_endow(MASTER, self.escrow.address, 100 * UNIT)
        self.assertEqual(self.totalVestedBalance(), 0)
        self.appendVestingEntry(MASTER, bob, time + 100, UNIT)
        self.assertEqual(self.totalVestedBalance(), UNIT)


        self.appendVestingEntry(MASTER, alice, time + 100, UNIT)
        self.assertEqual(self.totalVestedBalance(), 2 * UNIT)

        self.purgeAccount(MASTER, alice)
        self.assertEqual(self.totalVestedBalance(), UNIT)

        k = 5
        for n in [100 * 2**i for i in range(k)]:
            self.appendVestingEntry(MASTER, alice, time + n, n)

        self.assertEqual(self.totalVestedBalance(), UNIT + 100 * (2**k - 1))
        fast_forward(110)
        self.vest(alice)
        self.assertEqual(self.totalVestedBalance(), UNIT + 100 * (2**k - 1) - 100)

    def test_numVestingEntries(self):
        alice = fresh_account()
        time = block_time()
        times = [time + to_seconds(weeks=i) for i in range(1, 6)]

        self.assertEqual(self.numVestingEntries(alice), 0)
        self.appendVestingEntry(MASTER, alice, times[0], UNIT)
        self.assertEqual(self.numVestingEntries(alice), 1)
        self.appendVestingEntry(MASTER, alice, times[1], UNIT)
        self.assertEqual(self.numVestingEntries(alice), 2)
        self.appendVestingEntry(MASTER, alice, times[2], UNIT)
        self.appendVestingEntry(MASTER, alice, times[3], UNIT)
        self.appendVestingEntry(MASTER, alice, times[4], UNIT)
        self.assertEqual(self.numVestingEntries(alice), 5)
        self.purgeAccount(MASTER, alice)
        self.assertEqual(self.numVestingEntries(alice), 0)

    def test_getVestingScheduleEntry(self):
        alice = fresh_account()
        time = block_time()
        self.appendVestingEntry(MASTER, alice, time + 100, 1);
        self.assertEqual(self.getVestingScheduleEntry(alice, 0), [time + 100, 1])

    def test_getNextVestingIndex(self):
        self.h_endow(MASTER, self.escrow.address, 100 * UNIT)
        alice = fresh_account()
        time = block_time()
        times = [time + to_seconds(weeks=i) for i in range(1, 6)]

        self.assertEqual(self.getNextVestingIndex(alice), 0)

        for i in range(len(times)):
            self.appendVestingEntry(MASTER, alice, times[i], UNIT)

        for i in range(len(times)):
            fast_forward(to_seconds(weeks=1) + 30)
            self.assertEqual(self.getNextVestingIndex(alice), i)
            self.vest(alice)
            self.assertEqual(self.getNextVestingIndex(alice), i+1)

    def test_getNextVestingEntry(self):
        self.h_endow(MASTER, self.escrow.address, 100 * UNIT)
        alice = fresh_account()
        time = block_time()
        entries = [[time + to_seconds(weeks=i), i * UNIT] for i in range(1, 6)]

        self.assertEqual(self.getNextVestingEntry(alice), [0,0])

        for i in range(len(entries)):
            self.appendVestingEntry(MASTER, alice, entries[i][0], entries[i][1])

        for i in range(len(entries)):
            fast_forward(to_seconds(weeks=1) + 30)
            self.assertEqual(self.getNextVestingEntry(alice), entries[i])
            self.vest(alice)
            self.assertEqual(self.getNextVestingEntry(alice), [0,0] if i == len(entries) - 1 else entries[i+1])


    def test_getNextVestingTime(self):
        self.h_endow(MASTER, self.escrow.address, 100 * UNIT)
        alice = fresh_account()
        time = block_time()
        entries = [[time + to_seconds(weeks=i), i * UNIT] for i in range(1, 6)]

        self.assertEqual(self.getNextVestingTime(alice), 0)

        for i in range(len(entries)):
            self.appendVestingEntry(MASTER, alice, entries[i][0], entries[i][1])

        for i in range(len(entries)):
            fast_forward(to_seconds(weeks=1) + 30)
            self.assertEqual(self.getNextVestingTime(alice), entries[i][0])
            self.vest(alice)
            self.assertEqual(self.getNextVestingTime(alice), 0 if i == len(entries) - 1 else entries[i+1][0])

    def test_getNextVestingQuantity(self):
        self.h_endow(MASTER, self.escrow.address, 100 * UNIT)
        alice = fresh_account()
        time = block_time()
        entries = [[time + to_seconds(weeks=i), i * UNIT] for i in range(1, 6)]

        self.assertEqual(self.getNextVestingQuantity(alice), 0)

        for i in range(len(entries)):
            self.appendVestingEntry(MASTER, alice, entries[i][0], entries[i][1])

        for i in range(len(entries)):
            fast_forward(to_seconds(weeks=1) + 30)
            self.assertEqual(self.getNextVestingQuantity(alice), entries[i][1])
            self.vest(alice)
            self.assertEqual(self.getNextVestingQuantity(alice), 0 if i == len(entries) - 1 else entries[i+1][1])

    def test_feePool(self):
        pass
        """
        self.make_nomin_velocity()
        self.h_endow(MASTER, self.escrow.address, self.h_totalSupply() - (100 * UNIT))
        self.h_endow(MASTER, MASTER, 100 * UNIT)
        uncollected = self.n_feePool()
        self.assertClose(uncollected, 36 * UNIT)
        self.assertEqual(self.feePool(), 0)
        self.h_transfer(MASTER, self.escrow.address, UNIT)

        self.h_transfer(MASTER, self.escrow.address, UNIT)
        target_period = self.h_targetFeePeriodDurationSeconds() + 1000
        fast_forward(seconds=target_period)
        self.h_transfer(MASTER, self.escrow.address, UNIT)
        fast_forward(seconds=target_period)
        self.h_transfer(MASTER, self.escrow.address, UNIT)
        fast_forward(seconds=target_period)
        self.h_transfer(MASTER, self.escrow.address, UNIT)
        print(self.h_balanceOf(MASTER))
        self.withdrawContractFees(MASTER)
        self.assertEqual(self.feePool(), uncollected)
        """

    def test_setHavven(self):
        alice = fresh_account()
        self.setHavven(MASTER, alice)
        self.assertEqual(self.e_havven(), alice)
        self.assertReverts(self.setHavven, alice, alice)

    def test_setNomin(self):
        alice = fresh_account()
        self.setNomin(MASTER, alice)
        self.assertEqual(self.e_nomin(), alice)
        self.assertReverts(self.setNomin, alice, alice)

    def test_remitFees(self):
        pass

    def test_withdrawContractFees(self):
        pass

    def test_withdrawFees(self):
        pass

    def test_purgeAccount(self):
        pass

    def test_withdrawHavvens(self):
        pass

    def test_appendVestingEntry(self):
        alice, bob = fresh_accounts(2)
        amount = 16 * UNIT
        self.h_endow(MASTER, self.escrow.address, amount)
        time = block_time()

        # Should not be able to vest in the past
        self.assertReverts(self.appendVestingEntry, MASTER, alice, 0, UNIT)
        self.assertReverts(self.appendVestingEntry, MASTER, alice, time - 1, UNIT)
        self.assertReverts(self.appendVestingEntry, MASTER, alice, time, UNIT)

        # Vesting quantities should be nonzero
        self.assertReverts(self.appendVestingEntry, MASTER, alice, time+to_seconds(weeks=2), 0)

        self.appendVestingEntry(MASTER, alice, time+to_seconds(weeks=2), amount)
        self.vest(alice)
        self.assertEqual(self.h_balanceOf(alice), 0)
        fast_forward(weeks=3)
        self.vest(alice)
        self.assertEqual(self.h_balanceOf(alice), amount)
        self.h_transfer(alice, MASTER, amount)

        time = block_time()
        t1 = time+to_seconds(weeks=1)
        t2 = time+to_seconds(weeks=2)
        self.appendVestingEntry(MASTER, alice, t1, amount)
        self.assertReverts(self.appendVestingEntry, MASTER, alice, time+to_seconds(days=1), amount)
        self.assertReverts(self.appendVestingEntry, MASTER, alice, time+to_seconds(weeks=1), amount)
        self.appendVestingEntry(MASTER, alice, t2, amount + 1)

        self.assertEqual(self.getVestingQuantity(alice, 1), amount)
        self.assertEqual(self.getVestingQuantity(alice, 2), amount + 1)

        self.assertEqual(self.getVestingTime(alice, 1), t1)
        self.assertEqual(self.getVestingTime(alice, 2), t2)
        self.assertEqual(self.numVestingEntries(alice), 3)

    def test_addVestingSchedule(self):
        pass

    def test_vest(self):
        pass

    def test_fee_rollover(self):
        pass


if __name__ == '__main__':
    unittest.main()
